"""replenish_backlog must not propose meditation work for already-LIVE metrics.

parse_candidates skipped a row only when its status was the exact bare string
"LIVE". Real METRICS.md rows use "**LIVE**", "LIVE (approx)", and
"**LIVE** (AUTO-005 …)" — none of which equal "LIVE", so those live metrics
leaked into PROPOSED_BACKLOG.json as backlog items proposing instrumentation
that already exists.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import replenish_backlog as rb  # type: ignore[import-not-found]


METRICS = """\
# Bow — Operations

| Metric | Measures | Source | Status |
| --- | --- | --- | --- |
| Error_Rate | error frequency | telemetry | **LIVE** |
| Slop_Density | slop per 1k words | scan | LIVE (approx) |
| Agent_Time_Saved | hours saved | ledger | **LIVE** (AUTO-005, 2026-07-09) |
| Ghost_Metric | brand-new idea | none | SIMULATED |
"""


def _candidate_titles(tmp_path, monkeypatch):
    f = tmp_path / "METRICS.md"
    f.write_text(METRICS, encoding="utf-8")
    monkeypatch.setattr(rb, "METRICS_MD", f)
    return {c["title"] for c in rb.parse_candidates(set())}


def test_bold_live_metric_is_skipped(tmp_path, monkeypatch):
    assert "Error_Rate" not in _candidate_titles(tmp_path, monkeypatch)


def test_live_approx_metric_is_skipped(tmp_path, monkeypatch):
    assert "Slop_Density" not in _candidate_titles(tmp_path, monkeypatch)


def test_bold_live_with_trailing_note_is_skipped(tmp_path, monkeypatch):
    assert "Agent_Time_Saved" not in _candidate_titles(tmp_path, monkeypatch)


def test_non_live_metric_is_still_proposed(tmp_path, monkeypatch):
    assert "Ghost_Metric" in _candidate_titles(tmp_path, monkeypatch)
