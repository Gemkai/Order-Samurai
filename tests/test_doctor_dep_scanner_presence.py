"""Doctor's dependency-scanner presence check (execution/doctor.py).

IMPSYS-55: the AgenticaOS dev venv can silently lose pip-audit even though
Order Samurai's pyproject.toml declares it, because the venv installs from
the monorepo's requirements-dev.txt rather than that package's own pyproject
(the two CAN drift, though requirements-dev.txt gained its own pip-audit pin
on 2026-08-02, `75543600`, specifically to keep them aligned). When pip-audit
is missing, bin/codebase_deps_audit.py correctly detects it and refuses to
claim zero vulnerabilities -- but only when someone runs it. doctor.py had no
check for this at all; these tests pin the new one.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution.doctor import _run_dep_scanner_presence_checks  # noqa: E402


def test_warns_when_pip_audit_is_not_importable():
    rows = _run_dep_scanner_presence_checks(finder=lambda _name: None)
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert rows[0]["label"] == "dep-scanner-presence"
    assert "pip-audit" in rows[0]["detail"]
    assert "codebase_deps_audit" in rows[0]["detail"]


def test_ok_when_pip_audit_is_importable():
    rows = _run_dep_scanner_presence_checks(finder=lambda _name: object())
    assert len(rows) == 1
    assert rows[0]["status"] == "OK"
    assert rows[0]["label"] == "dep-scanner-presence"


def test_a_lookup_error_is_treated_as_absent_not_a_crash():
    # importlib.util.find_spec raises ValueError for some malformed names and
    # ImportError for some parent-package failures -- either must WARN, not
    # blow up doctor's whole run.
    def _raises(_name):
        raise ValueError("bad spec name")

    rows = _run_dep_scanner_presence_checks(finder=_raises)
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"


def test_live_pip_audit_presence_on_this_interpreter():
    # Reproduces the actual check live, whatever this interpreter's real
    # state is -- must always return exactly one row, never crash.
    rows = _run_dep_scanner_presence_checks()
    assert len(rows) == 1
    assert rows[0]["status"] in ("OK", "WARN")
