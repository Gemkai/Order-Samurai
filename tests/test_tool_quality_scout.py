"""M2/M3: the offline output-quality scout (bin/tool_quality_scout.py). Stubbed judges, no Ollama."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.tool_quality_scout import run_scout  # type: ignore[import-not-found]
from agentica_core.evals.tool_triad import SELECTION, ARGS, UTILIZATION
from agentica_core.evals.faithfulness import FAITHFULNESS, REFUSAL


def _valid_stub(**k):
    """Return a valid label for whichever judge is calling (each lists its labels in the system
    instruction). Order matters: pick the most specific label present."""
    sysp = k.get("system_instruction", "")
    for lbl in ("faithful", "relevant", "correct", "used_well", "appropriate"):
        if f"'{lbl}'" in sysp:
            return json.dumps({"label": lbl})
    return json.dumps({"label": "appropriate"})


def _write_transcript(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"role": "user", "content": "list files"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "running ls"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a.py"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "found a.py"}]}},
    ]), encoding="utf-8")


def test_scout_scores_tool_triad_and_faithfulness(tmp_path):
    proj = tmp_path / "projects"
    _write_transcript(proj)
    out = run_scout(proj, generate_fn=_valid_stub)
    # all five output-quality metrics present in one pass
    assert {SELECTION, ARGS, UTILIZATION, FAITHFULNESS, REFUSAL} <= set(out)
    assert out[SELECTION]["score"] == 1.0
    # the follow-up turn "found a.py" has the tool result "a.py" as context -> faithfulness scored
    assert out[FAITHFULNESS]["score"] == 1.0


def test_scout_refusal_gaps_without_a_refusal(tmp_path):
    proj = tmp_path / "projects"
    _write_transcript(proj)  # no refusal in this transcript
    out = run_scout(proj, generate_fn=_valid_stub)
    assert out[REFUSAL]["score"] == -1  # no refusals in scope -> honest gap


def test_scout_no_transcripts_all_gap(tmp_path):
    out = run_scout(tmp_path / "nope", generate_fn=_valid_stub, seed_cfg={"collection": "c", "top_k": 2, "queries": []})
    for key in (SELECTION, FAITHFULNESS, REFUSAL):
        assert out[key]["score"] == -1


def test_scout_includes_retrieval_with_injected_search(tmp_path):
    proj = tmp_path / "projects"
    _write_transcript(proj)
    seed = {"collection": "claude_skills", "top_k": 2, "queries": ["find a code-review skill"]}
    search = lambda q, *, top_k=2: ["ce-code-review", "security-audit"]
    out = run_scout(proj, generate_fn=_valid_stub, search_fn=search, seed_cfg=seed)
    assert "Retrieval_Relevance" in out
    assert out["Retrieval_Relevance"]["score"] == 1.0  # both chunks judged 'relevant'


def _write_big_session(d: Path, name: str, tool_count: int):
    """A session with `tool_count` tool uses, each result answered by a text message."""
    d.mkdir(parents=True, exist_ok=True)
    entries = [{"type": "user", "message": {"role": "user", "content": "do work"}}]
    for i in range(tool_count):
        entries += [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": "out"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"step {i} done"}]}},
        ]
    (d / f"{name}.jsonl").write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_scout_spreads_triad_budget_across_sessions(tmp_path):
    # One giant session must not monopolize the triad budget (production showed
    # "n=20 across 1 sessions"): with the per-session cap, both sessions contribute.
    proj = tmp_path / "projects"
    _write_big_session(proj, "big1", 40)
    _write_big_session(proj, "big2", 40)
    out = run_scout(proj, generate_fn=_valid_stub, max_tool_uses=12,
                    seed_cfg={"collection": "c", "top_k": 2, "queries": []})
    assert "across 2 sessions" in out[SELECTION]["explanation"]


def test_scout_budget_counts_accepted_judgments_not_attempts(tmp_path):
    # A gapping judge must not eat the budget: with every selection judgment gapped,
    # the scout keeps attempting up to the 3x attempt ceiling instead of stopping at
    # max_tool_uses attempts, and the result is an honest gap with n=0.
    proj = tmp_path / "projects"
    _write_big_session(proj, "big1", 10)

    def gap_stub(**k):
        return json.dumps({"label": "bogus"})

    out = run_scout(proj, generate_fn=gap_stub, max_tool_uses=5,
                    seed_cfg={"collection": "c", "top_k": 2, "queries": []})
    assert out[SELECTION]["score"] == -1
    assert out[SELECTION]["n"] == 0


def test_scout_output_carries_accepted_n(tmp_path):
    proj = tmp_path / "projects"
    _write_transcript(proj)
    out = run_scout(proj, generate_fn=_valid_stub,
                    seed_cfg={"collection": "c", "top_k": 2, "queries": []})
    assert out[SELECTION]["n"] == 1
    assert out[REFUSAL]["n"] == 0  # gap -> n=0
