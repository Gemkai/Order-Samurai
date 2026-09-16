"""Tests for the backlog replenisher (replenish_backlog.py).

When the approved backlog empties, this proposes new metric candidates parsed
from METRICS.md, skipping any already tracked in MEDITATION_STATE. These tests
pin that a malformed backlog item (empty / missing title) does not silently
suppress every candidate — an empty existing title must never match everything.
"""
import importlib.util
import json
from pathlib import Path

_REPL = Path(__file__).resolve().parents[1] / "bin" / "replenish_backlog.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("replenish_backlog", _REPL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_METRICS_MD = """\
# Bow

| Metric | Measures | Source | Status |
| --- | --- | --- | --- |
| Brand_New_Metric | measures a brand new thing | telemetry.x | +SCOUT |
"""


def test_empty_existing_title_does_not_suppress_all_candidates(tmp_path, monkeypatch):
    """A backlog item with an empty title puts "" into existing_titles. The old
    containment check `existing in title_lower` is True for every candidate when
    existing == "" (empty string is a substring of everything), so a single
    titleless item silently blocked ALL replenishment. The candidate must still
    be parsed."""
    mod = _load_module()
    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    candidates = mod.parse_candidates(existing_titles={"", "some unrelated metric"})

    titles = {c["title"] for c in candidates}
    assert "Brand_New_Metric" in titles


def test_real_existing_title_still_suppresses_its_candidate(tmp_path, monkeypatch):
    """Sanity: a genuine matching title still suppresses the candidate — the fix
    only stops the empty-string false positive, not real dedup."""
    mod = _load_module()
    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    candidates = mod.parse_candidates(existing_titles={"brand_new_metric"})

    assert candidates == []


_METRICS_MD_3COL = """\
## Harness-derived expansion

### Sword

| Metric | Signal source | Status |
|--------|---------------|--------|
| Guardrail Blocks | guardrails.py + guardrail_patterns.json (PreToolUse) | +STREAM |
"""


def test_three_column_table_rows_are_not_silently_dropped(tmp_path, monkeypatch):
    """METRICS.md's 'Harness-derived expansion' section uses a 3-column table
    (Metric | Signal source | Status), unlike the main registry's 4-column
    (Metric | Measures | Source | Status). ROW_RE required exactly 4 columns,
    so every row in the 3-column section silently failed to match and could
    never be proposed to the backlog — defeating the whole point of the
    function (find metric rows not yet tracked)."""
    mod = _load_module()
    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD_3COL, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    candidates = mod.parse_candidates(existing_titles=set())

    titles = {c["title"] for c in candidates}
    assert "Guardrail Blocks" in titles


def test_promoted_auto_ids_are_not_reissued(tmp_path, monkeypatch):
    """The next id must clear BOTH files.

    Historically `ronin promote` MOVED approved items out of PROPOSED_BACKLOG.json
    into MEDITATION_STATE.json, so numbering from the proposals alone restarted the
    counter after every promote and re-issued an id the backlog already held — the
    live state still carries a duplicate AUTO-041 from exactly that path. Since
    2026-08-08 promote is an in-place status flip and moves nothing, so that failure
    can no longer recur; this test still pins the two-file reservation because the
    historical MEDITATION_STATE ids remain live and must never be reissued."""
    mod = _load_module()
    state = tmp_path / "MEDITATION_STATE.json"
    state.write_text(
        json.dumps({"backlog": [{"id": "AUTO-001", "title": "Already promoted metric"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "MEDITATION_STATE", state)
    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    candidates = mod.parse_candidates(existing_titles=mod.load_existing_titles())
    items = mod.build_items(candidates, proposed_items=[], auto_approve=False)

    assert [i["id"] for i in items] == ["AUTO-002"]


def test_proposed_ids_still_advance_the_counter(tmp_path, monkeypatch):
    """Sanity: the un-promoted proposals still reserve their ids — the fix widens
    the id universe, it does not replace it."""
    mod = _load_module()
    state = tmp_path / "MEDITATION_STATE.json"
    state.write_text(json.dumps({"backlog": []}), encoding="utf-8")
    monkeypatch.setattr(mod, "MEDITATION_STATE", state)
    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    candidates = mod.parse_candidates(existing_titles=set())
    items = mod.build_items(candidates, proposed_items=[{"id": "AUTO-007"}], auto_approve=False)

    assert [i["id"] for i in items] == ["AUTO-008"]


# ── F7: WIP cap enforced at the generator ───────────────────────────────────

def test_pending_count_counts_only_unblessed_proposed_items():
    items = [
        {"status": "proposed", "approved": False},   # awaiting a decision
        {"status": "proposed", "approved": True},     # blessed, awaiting `ronin promote`
        {"status": "approved_for_work", "approved": True},  # already promoted
    ]
    mod = _load_module()
    assert mod.pending_count(items) == 1


def test_seeded_over_cap_backlog_logs_throttle_skip_and_appends_nothing(
    tmp_path, monkeypatch, capsys
):
    """Seed PROPOSED_BACKLOG.json above --cap (a tmp_path fixture, never the real
    repo state) and confirm main() logs a throttle-skip and does not append a new
    candidate instead of silently proposing more on top of an already-overfull
    backlog."""
    mod = _load_module()

    metrics = tmp_path / "METRICS.md"
    metrics.write_text(_METRICS_MD, encoding="utf-8")
    monkeypatch.setattr(mod, "METRICS_MD", metrics)

    meditation_state = tmp_path / "MEDITATION_STATE.json"
    meditation_state.write_text(json.dumps({"backlog": []}), encoding="utf-8")
    monkeypatch.setattr(mod, "MEDITATION_STATE", meditation_state)

    proposed_backlog = tmp_path / "PROPOSED_BACKLOG.json"
    seeded_items = [
        {"id": f"AUTO-{i:03d}", "title": f"Pre-existing pending item {i}",
         "status": "proposed", "approved": False}
        for i in range(1, mod.DEFAULT_PENDING_CAP + 3)   # comfortably over the cap
    ]
    proposed_backlog.write_text(
        json.dumps({"generated_at": "", "items": seeded_items}), encoding="utf-8")
    monkeypatch.setattr(mod, "PROPOSED_BACKLOG", proposed_backlog)
    monkeypatch.setattr(mod, "BACKLOG_LOCK",
                         proposed_backlog.with_name(proposed_backlog.name + ".lock"))

    monkeypatch.setattr("sys.argv", ["replenish_backlog.py"])

    mod.main()

    out = capsys.readouterr().out
    assert "THROTTLE-SKIP" in out

    on_disk = json.loads(proposed_backlog.read_text())["items"]
    assert on_disk == seeded_items, "no new candidate should have been appended"
