"""Context_Cliff_Events (AUTO-011) — absolute >140k-token context-pressure count from transcripts."""
import json

import agentica_core.aggregate as agg


def _session(pd, name, ctx_totals):
    """Write assistant lines whose usage.input_tokens equals each given total."""
    lines = [json.dumps({"type": "assistant",
                         "message": {"model": "claude-opus-4-8", "usage": {"input_tokens": c}}})
             for c in ctx_totals]
    (pd / name).write_text("\n".join(lines), encoding="utf-8")


def _projects(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    pd = tmp_path / ".claude" / "projects"
    pd.mkdir(parents=True)
    return pd


def test_counts_sessions_over_threshold(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "a.jsonl", [100_000, 150_000])  # max 150k > 140k -> cliff
    _session(pd, "b.jsonl", [50_000, 90_000])    # max 90k -> not a cliff
    # Share since 2026-07-19: 1 cliff of 2 scanned sessions = 50%
    assert agg.r_context_cliff_events([]) == 50.0


def test_sums_all_three_token_fields(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    (pd / "c.jsonl").write_text(json.dumps({"type": "assistant", "message": {"usage": {
        "input_tokens": 50_000, "cache_read_input_tokens": 60_000, "cache_creation_input_tokens": 40_000,
    }}}), encoding="utf-8")  # 150k total > 140k -> cliff
    assert agg.r_context_cliff_events([]) == 100.0  # 1 of 1 scanned = 100%


def test_none_when_no_transcripts(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # projects dir never created
    assert agg.r_context_cliff_events([]) is None


def test_none_when_no_usage_data(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    (pd / "d.jsonl").write_text(json.dumps({"type": "user", "message": {"content": "hi"}}), encoding="utf-8")
    assert agg.r_context_cliff_events([]) is None  # no usage-bearing assistant msgs -> gap, not 0


def test_registered_brush_derived():
    e = next((x for x in agg.REGISTRY if x[2] == "Context_Cliff_Events"), None)
    assert e is not None and e[0] == "brush" and e[4] == "DERIVED"
    from agentica_core.insights import METRIC_CONFIG
    assert METRIC_CONFIG["Context_Cliff_Events"]["dir"] == "lower"
