"""doctor_last.json carries every WARN row, not only the gating FAILs (coverage-review W6).

Sol 5.6's plan review (2026-09-02, finding 5): the spectator families this revamp adds
(activation drift, shared-checkout health, scheduled-run outcomes, incident coverage)
report almost entirely in WARN, and `write_state` persisted `fails` only — so the
dashboard could never have shown any of them. Both tests failed before the change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import doctor  # noqa: E402


def _family(name, results, gating=False, warns=True):
    return doctor._Family(name, lambda: results, gating, warns=warns)


def test_run_report_collects_warn_rows_from_counting_and_non_counting_families():
    families = (
        _family("alpha", [{"status": "WARN", "label": "alpha.slow", "detail": "one"}], warns=True),
        _family("beta", [{"status": "WARN", "label": "beta.quiet", "detail": "two"}], warns=False),
        _family("gamma", [{"status": "OK", "label": "gamma.fine", "detail": "-"},
                          {"status": "FAIL", "label": "gamma.bad", "detail": "three"}], gating=True),
    )
    report = doctor.run_report(families)
    # The counting rule is untouched: beta's WARN is recorded but not counted.
    assert report.counts == {"OK": 1, "WARN": 1, "FAIL": 1, "ERROR": 0}
    assert report.warns == [
        {"family": "alpha", "label": "alpha.slow", "detail": "one",
         "status": "WARN", "gating": False},
        {"family": "beta", "label": "beta.quiet", "detail": "two",
         "status": "WARN", "gating": False},
    ]
    assert [f["label"] for f in report.fails] == ["gamma.bad"]


def test_write_state_persists_warns_beside_fails(tmp_path):
    report = doctor.DoctorReport(
        lines=[], counts={"OK": 0, "WARN": 1, "FAIL": 0}, exit_code=0, fails=[],
        warns=[{"family": "activation-drift", "label": "activation-drift.3001",
                "detail": "source newer than process"}],
    )
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["fails"] == []
    assert payload["warns"] == [{"family": "activation-drift", "label": "activation-drift.3001",
                                 "detail": "source newer than process"}]
    # The pre-existing reader contract (counts + fails) is unchanged.
    assert set(payload) >= {"generated_at", "exit_code", "counts", "fails", "producer"}
