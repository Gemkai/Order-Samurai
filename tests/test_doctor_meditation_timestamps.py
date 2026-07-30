"""Tests for the doctor's meditation-timestamp check (execution/doctor.py).

Calibration coefficients for the bow hero (Estimated_Agent_Time_Saved) accumulate
only from (started_at, completed_at) pairs on backlog items. This row exists to
protect that sample flow.

It reported a bare `[OK] all done/doing backlog items carry calibration
timestamps` whenever nothing was wrong — including when there were no done/doing
items AT ALL, which satisfies "all of them" trivially. Observed 2026-07-29: 9
backlog items, every one `todo`, cycle 0, coefficients frozen at 4/3/4 of a
10-sample bar, and this row said OK. "Nothing to check" is not "checked and
healthy", and the two must not print the same thing.

The severity is deliberately OK in both branches, which the last test pins:
doctor's WARN count feeds the meditation cycle's A-prime gate (halt when current
WARN > baseline), so warning here would halt the very cycle that produces the
missing samples.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

import execution.doctor as doctor  # noqa: E402


def _state(tmp_path: Path, backlog: list[dict], monkeypatch) -> None:
    """Point the check at a synthetic MEDITATION_STATE.json."""
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "MEDITATION_STATE.json").write_text(
        json.dumps({"backlog": backlog}), encoding="utf-8")
    monkeypatch.setattr(doctor, "ROOT_DIR", tmp_path)


def _row(results: list[dict], label_contains: str) -> dict | None:
    return next((r for r in results if label_contains in r["label"]), None)


def test_an_empty_backlog_reports_zero_samples_rather_than_health(tmp_path, monkeypatch):
    _state(tmp_path, [{"id": "a", "kind": "stream", "status": "todo"}], monkeypatch)
    results = doctor._run_meditation_timestamp_checks()
    row = _row(results, "no-samples")
    assert row is not None, f"expected a no-samples row, got {results}"
    assert "0 done/doing items" in row["detail"]
    assert "1 backlog item" in row["detail"]
    # The claim that made it vacuous must be gone.
    assert "all done/doing backlog items carry" not in row["detail"]


def test_a_stamped_item_reports_the_population_it_checked(tmp_path, monkeypatch):
    _state(tmp_path, [
        {"id": "a", "kind": "stream", "status": "done",
         "started_at": "2026-07-01T00:00:00Z", "completed_at": "2026-07-01T01:00:00Z"},
        {"id": "b", "kind": "scout", "status": "todo"},
    ], monkeypatch)
    results = doctor._run_meditation_timestamp_checks()
    row = _row(results, "meditation-timestamps")
    assert row is not None and row["status"] == "OK"
    # Names how many it actually verified, so an OK over 1 item cannot read the
    # same as an OK over 50.
    assert "all 1 done/doing backlog item(s)" in row["detail"]


def test_a_missing_timestamp_still_warns(tmp_path, monkeypatch):
    """The negative control: the row must not have been muted into always-OK."""
    _state(tmp_path, [{"id": "a", "kind": "stream", "status": "doing"}], monkeypatch)
    results = doctor._run_meditation_timestamp_checks()
    assert any(r["status"] == "WARN" for r in results), results


def test_the_no_samples_row_never_warns(tmp_path, monkeypatch):
    """doctor's WARN count feeds the meditation cycle's A-prime halt gate. A WARN
    here would stop the cycle that creates the samples whose absence it reports —
    the circular shape this row was rewritten to expose, not one to add."""
    _state(tmp_path, [{"id": "a", "kind": "stream", "status": "todo"}], monkeypatch)
    results = doctor._run_meditation_timestamp_checks()
    assert [r["status"] for r in results] == ["OK"], results


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
