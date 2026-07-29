#!/usr/bin/env python3
"""tool_quality_scout — offline scorer for the Arts OUTPUT-QUALITY metrics.

Loads recent session transcripts ONCE and runs every evals judge over them — the tool-use triad
(evals.tool_triad) plus faithfulness & refusal-appropriateness (evals.faithfulness) — writing all
aggregate scores to state/tool_quality.json. The dashboard REGISTRY reducers just READ that file,
so the expensive LLM-judge work stays OFF the 15-minute refresh hot path (this scout is scheduled
nightly via the meditation cycle). Honest gaps: a metric with no scorable data is written as
score -1, which the reducer maps to SIMULATED (never a fabricated 0).

Judging routes through the gateway (evals uses local_guards; faithfulness escalates to the
red_team tier). Runnable standalone; `run_scout` accepts an injected generate_fn for tests.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# agentica_core lives under Governance/ (bin/ -> Order Samurai -> Governance)
_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))

from agentica_core.evals.tool_triad import (  # noqa: E402
    ARGS, SELECTION, UTILIZATION, build_judges, score_tool_uses,
)
from agentica_core.evals.faithfulness import (  # noqa: E402
    FAITHFULNESS, REFUSAL, build_faithfulness_judge, build_refusal_judge,
    score_faithfulness, score_refusals,
)
from agentica_core.evals.retrieval import (  # noqa: E402
    build_retrieval_judge, score_retrieval, qdrant_search,
)
from agentica_core.evals.score import Score  # noqa: E402
from agentica_core.evals.transcript_source import iter_sessions  # noqa: E402
from agentica_core.atomic import atomic_json_write  # noqa: E402
from agentica_core import harness_config  # noqa: E402

_STATE = Path(__file__).resolve().parents[1] / "state" / "tool_quality.json"
_SEED = Path(__file__).resolve().parents[1] / "config" / "retrieval_seed_queries.json"


def _load_seed_queries() -> dict:
    """Read the curated retrieval seed set; {} queries when absent (metric gaps -> SIMULATED)."""
    try:
        d = json.loads(_SEED.read_text(encoding="utf-8"))
        return {
            "collection": d.get("collection", "claude_skills"),
            "top_k": int(d.get("top_k", 5)),
            "queries": [q for q in d.get("queries", []) if isinstance(q, str)],
        }
    except (OSError, ValueError):
        return {"collection": "claude_skills", "top_k": 5, "queries": []}


def _default_max_files() -> int:
    """Declared transcript-scan bound from the editable surface; 60 is the fallback literal."""
    try:
        return int(harness_config.get_value("scout_max_files"))
    except (OSError, ValueError, KeyError):
        return 60


# The evals _aggregate helpers stamp the judgment count as `n=<K>` in the
# explanation (tool_triad._aggregate / faithfulness._aggregate) — that is the
# contract _judged_n parses to weight per-session means into one flat mean.
_N_RE = re.compile(r"n=(\d+)")


def _judged_n(s: Score) -> int:
    """Judgment count behind a per-session aggregate Score; 0 for a gap session."""
    if s.is_gap:
        return 0
    m = _N_RE.search(s.explanation)
    return int(m.group(1)) if m else 1


def _combine(name: str, parts: list[Score], gap_explanation: str) -> Score:
    """Fold per-session aggregate Scores into one metric Score.

    Weighting each session mean by its judgment count makes the combined mean
    identical to the flat mean over every individual accepted judgment — the exact
    aggregation the old single-pass produced, minus the cross-session pairs. Gap
    sessions weigh n=0 (excluded), and an all-gap fold returns the honest -1 gap."""
    total = sum(s.score * _judged_n(s) for s in parts)
    n = sum(_judged_n(s) for s in parts)
    if n == 0:
        return Score(name, -1.0, "no_data", gap_explanation, "llm", "maximize")
    k = sum(1 for s in parts if not s.is_gap)
    return Score(name, total / n, "aggregate", f"mean over n={n} across {k} sessions", "llm", "maximize")


def run_scout(
    projects_dir: Optional[Path] = None, *, generate_fn=None, search_fn=None, seed_cfg=None,
    max_files: Optional[int] = None, max_tool_uses: int = 50, max_judgments: int = 50,
) -> dict:
    """Score recent transcripts across all output-quality metrics; return
    {metric: {score, label, explanation}}. score is 0-1, or -1 for a gap (no scorable data /
    judge unavailable). Bounds (env-overridable for the scheduled job): TOOL_QUALITY_MAX_FILES,
    TOOL_QUALITY_MAX_TOOL_USES. `generate_fn` (tests only) drives every judge.

    max_files defaults to the declared `scout_max_files` surface knob; an explicit argument or
    the TOOL_QUALITY_MAX_FILES env override still wins, in that order."""
    if max_files is None:
        max_files = _default_max_files()
    max_files = int(os.environ.get("TOOL_QUALITY_MAX_FILES", max_files))
    max_tool_uses = int(os.environ.get("TOOL_QUALITY_MAX_TOOL_USES", max_tool_uses))
    max_judgments = int(os.environ.get("TOOL_QUALITY_MAX_JUDGMENTS", max_judgments))
    # Score each session in isolation: the scorers pair turns[i+1] as the follow-up,
    # so a flat load_turns() stream would judge pairs that straddle two unrelated
    # sessions. Each metric family keeps its own global budget across sessions.
    judges = build_judges(generate_fn=generate_fn)
    faith_judge = build_faithfulness_judge(generate_fn=generate_fn)
    refusal_judge = build_refusal_judge(generate_fn=generate_fn)
    triad_parts: dict[str, list[Score]] = {SELECTION: [], ARGS: [], UTILIZATION: []}
    faith_parts: list[Score] = []
    refusal_parts: list[Score] = []
    uses_left, faith_left, refusal_left = max_tool_uses, max_judgments, max_judgments
    # Two starvation guards (2026-07-19, judge-harness fix):
    # - per-session cap: one giant agentic session previously consumed the whole triad
    #   budget ("n=20 across 1 sessions") — cap each session so >=6 contribute.
    # - accepted-counting: uses_left tracks ACCEPTED selection judgments, not attempts;
    #   with the local judge's gap rate, attempt-counting silently shrank n. A separate
    #   attempt ceiling (3x) keeps a pathological all-gap window bounded.
    per_session_cap = max(5, max_tool_uses // 6)
    triad_attempts_left = 3 * max_tool_uses
    for session in iter_sessions(projects_dir, max_files=max_files):
        if (uses_left <= 0 or triad_attempts_left <= 0) and faith_left <= 0 and refusal_left <= 0:
            break
        if uses_left > 0 and triad_attempts_left > 0:
            attempt_budget = min(per_session_cap, triad_attempts_left)
            session_scores = score_tool_uses(session, judges, max_tool_uses=attempt_budget)
            for name, s in session_scores.items():
                triad_parts[name].append(s)
            triad_attempts_left -= min(attempt_budget, sum(len(t.tool_uses) for t in session))
            uses_left -= _judged_n(session_scores[SELECTION])
        if faith_left > 0:
            s = score_faithfulness(session, faith_judge, max_judgments=faith_left)
            faith_parts.append(s)
            faith_left -= _judged_n(s)
        if refusal_left > 0:
            s = score_refusals(session, refusal_judge, max_judgments=refusal_left)
            refusal_parts.append(s)
            refusal_left -= _judged_n(s)

    scores = {name: _combine(name, parts, "no tool uses scored in window")
              for name, parts in triad_parts.items()}
    scores[FAITHFULNESS] = _combine(FAITHFULNESS, faith_parts, "no scorable turns in window")
    scores[REFUSAL] = _combine(REFUSAL, refusal_parts, "no scorable turns in window")

    # Retrieval relevance (M4) — Qdrant seed-set benchmark, independent of the transcripts above.
    seed = seed_cfg if seed_cfg is not None else _load_seed_queries()
    if seed["queries"]:
        coll, k = seed["collection"], seed["top_k"]
        search = search_fn if search_fn is not None else (
            lambda q, *, top_k=k: qdrant_search(q, collection=coll, top_k=top_k))
        ret = score_retrieval(seed["queries"], build_retrieval_judge(generate_fn=generate_fn),
                              search_fn=search, top_k=k, max_pairs=max_judgments)
        scores[ret.name] = ret

    return {
        # "n" = accepted judgment count behind the aggregate — the reducer's min-sample
        # guard (judge_min_n surface knob) reads it; -1 gaps carry n=0.
        name: {"score": s.score, "label": s.label, "explanation": s.explanation,
               "n": _judged_n(s)}
        for name, s in scores.items()
    }


def main() -> int:
    metrics = run_scout()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "~/.claude/projects/**/*.jsonl (bounded) via evals.tool_triad",
        "metrics": metrics,
    }
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(_STATE, payload)  # tmp+replace — no torn read while the dashboard refresh reads it
    live = sum(1 for m in metrics.values() if m["score"] >= 0)
    print(f"tool_quality_scout: wrote {_STATE} ({live}/{len(metrics)} metrics scored, rest SIMULATED)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
