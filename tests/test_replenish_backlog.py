"""Tests for the backlog replenisher (replenish_backlog.py).

When the approved backlog empties, this proposes new metric candidates parsed
from METRICS.md, skipping any already tracked in MEDITATION_STATE. These tests
pin that a malformed backlog item (empty / missing title) does not silently
suppress every candidate — an empty existing title must never match everything.
"""
import importlib.util
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
