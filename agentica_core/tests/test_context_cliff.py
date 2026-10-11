"""Context_Cliff_Events (AUTO-011) — absolute >140k-token context-pressure count from transcripts."""
import json

import pytest

import agentica_core.aggregate as agg


@pytest.fixture(autouse=True)
def _fresh_context_cliff_memo(monkeypatch):
    monkeypatch.setattr(agg, "_CONTEXT_CLIFF_MEMO", None)
    monkeypatch.setattr(agg, "_CONTEXT_CLIFF_TTL_S", 60.0)


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


def test_transcript_vanishing_before_stat_is_skipped(tmp_path, monkeypatch):
    """A transcript deleted between rglob and stat (factory worktree cleanup) is skipped,
    not raised — a FileNotFoundError here used to fail the whole aggregate() refresh."""
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "kept.jsonl", [150_000])
    _session(pd, "gone.jsonl", [50_000])  # would drop the share to 50.0 if scanned
    real_stat = agg.Path.stat

    def racing_stat(self, *args, **kwargs):
        if self.name == "gone.jsonl":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(agg.Path, "stat", racing_stat)
    assert agg.r_context_cliff_events([]) == 100.0  # only kept.jsonl scanned


def test_transcript_vanishing_before_read_is_skipped(tmp_path, monkeypatch):
    """Same race one step later: the file stats fine but is gone by open()."""
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "kept.jsonl", [150_000])
    _session(pd, "gone.jsonl", [50_000])
    real_open = open

    def racing_open(path, *args, **kwargs):
        if str(path).endswith("gone.jsonl"):
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(agg, "open", racing_open, raising=False)
    assert agg.r_context_cliff_events([]) == 100.0
    assert agg._CONTEXT_CLIFF_MEMO is None  # partial scan is not memoized


def test_unchanged_transcripts_reuse_the_memo(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "a.jsonl", [100_000])

    assert agg.r_context_cliff_events([]) == 0.0
    memo = agg._CONTEXT_CLIFF_MEMO
    assert agg.r_context_cliff_events([]) == 0.0

    assert agg._CONTEXT_CLIFF_MEMO is memo


def test_transcript_change_invalidates_the_memo(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "a.jsonl", [100_000])
    assert agg.r_context_cliff_events([]) == 0.0
    memo = agg._CONTEXT_CLIFF_MEMO

    _session(pd, "a.jsonl", [100_000, 200_000])

    assert agg.r_context_cliff_events([]) == 100.0
    assert agg._CONTEXT_CLIFF_MEMO is not memo


def test_context_cliff_memo_expires(tmp_path, monkeypatch):
    pd = _projects(tmp_path, monkeypatch)
    _session(pd, "a.jsonl", [100_000])
    assert agg.r_context_cliff_events([]) == 0.0
    memo = agg._CONTEXT_CLIFF_MEMO

    monkeypatch.setattr(agg, "_CONTEXT_CLIFF_TTL_S", -1.0)

    assert agg.r_context_cliff_events([]) == 0.0
    assert agg._CONTEXT_CLIFF_MEMO is not memo


def test_registered_brush_derived():
    e = next((x for x in agg.REGISTRY if x[2] == "Context_Cliff_Events"), None)
    assert e is not None and e[0] == "brush" and e[4] == "DERIVED"
    from agentica_core.insights import METRIC_CONFIG
    assert METRIC_CONFIG["Context_Cliff_Events"]["dir"] == "lower"
