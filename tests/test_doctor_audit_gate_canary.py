"""Doctor's audit-gate canary (execution/doctor.py).

The reflex engine spawns the maker-checker audit with a bare 'python3' from
its process PATH — not sys.executable — so a wrong PATH kills every audit at
import time (exit 2, 'audit_rejected') with nothing else noticing. The canary
re-proves the gate can start, the same way the engine spawns it, on every
doctor run. These tests pin: green live, [] when kill-switched, and a WARN
that names the interpreter / surfaces the gate's own module diagnosis when the
spawn fails.
"""
from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from _layout import governance_root

_GOVERNANCE = governance_root(__file__)
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution.doctor import (  # noqa: E402
    _resolve_engine_python_bin,
    _run_audit_gate_canary_checks,
)

_REAL_PLIST = (Path.home() / "Library" / "LaunchAgents" /
               "com.agentica.order-samurai-api.plist")


@pytest.mark.live_machine
def test_canary_is_green_live():
    # The continuously-verified claim itself: under the engine's own interpreter
    # resolution (bare python3 on this host's PATH), the gate starts and
    # approves a benign patch. If THIS fails, the Aug-1 PATH fix has regressed.
    #
    # live_machine (2026-08-11): resolution goes through the engine's real
    # LaunchAgents plist, so on a runner with no engine installed this asserts
    # OK against a host that cannot produce OK -- it fails with "could not
    # confirm this is the engine's actual interpreter", which is the correct
    # answer for that host, not a regression. The marker was already on
    # test_plist_kill_switch_reverts_to_doctors_own_path below, whose comment
    # cites "test_canary_is_green_live's own live-host assumption" -- the
    # assumption was identified there and simply never marked here. Nothing is
    # weakened: doctor.py runs this canary on every real doctor run, which is
    # the surface where the claim has to hold.
    rows = _run_audit_gate_canary_checks()
    assert len(rows) == 1
    assert rows[0]["label"] == "audit-gate-canary"
    assert rows[0]["status"] == "OK", rows[0]["detail"]


def test_kill_switch_disables_the_canary(monkeypatch):
    monkeypatch.setenv("AUDIT_CANARY_ENABLED", "false")
    assert _run_audit_gate_canary_checks() == []


def test_missing_interpreter_warns_naming_it():
    rows = _run_audit_gate_canary_checks(python_bin="/nonexistent/python3-canary")
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "/nonexistent/python3-canary" in rows[0]["detail"]
    assert "audit_rejected" in rows[0]["detail"]


def test_nonzero_exit_warns_surfacing_the_gate_diagnosis(tmp_path):
    # A stand-in interpreter that fails the way the CommandLineTools python did:
    # exit 2 with the gate's module-naming message. The WARN must carry that
    # first line so the fix is actionable from doctor output alone.
    fake = tmp_path / "python3"
    fake.write_text(
        "#!/bin/sh\n"
        "echo \"Error: Module 'requests' not found under interpreter /usr/bin/python3\"\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    rows = _run_audit_gate_canary_checks(python_bin=str(fake))
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "exited 2" in rows[0]["detail"]
    assert "Module 'requests'" in rows[0]["detail"]


def test_missing_script_warns(tmp_path):
    rows = _run_audit_gate_canary_checks(script=tmp_path / "not_there.py")
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "missing" in rows[0]["detail"]


def _write_plist(path: Path, path_value: str) -> Path:
    import plistlib

    path.write_bytes(plistlib.dumps({"EnvironmentVariables": {"PATH": path_value}}))
    return path


def test_resolve_engine_python_bin_uses_the_plist_path(tmp_path):
    # The core of the fix: resolution must go through the PLIST's PATH, not
    # doctor's own -- so point a fake plist at a dir with a distinctive
    # interpreter stand-in and confirm THAT one comes back.
    bin_dir = tmp_path / "engine_bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python3"
    fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    plist = _write_plist(tmp_path / "fake.plist", str(bin_dir))

    resolved, warning = _resolve_engine_python_bin("python3", plist_path=plist)
    assert resolved == str(fake_python)
    assert warning is None


def test_resolve_engine_python_bin_falls_back_when_plist_missing(tmp_path):
    # Falls back to sys.executable, NOT a bare 'python3': the bare name resolves
    # against whatever PATH the caller happens to have, which is exactly the
    # unknown-interpreter condition this function exists to eliminate.
    resolved, warning = _resolve_engine_python_bin(
        "python3", plist_path=tmp_path / "does_not_exist.plist"
    )
    assert resolved == sys.executable
    assert warning is not None
    assert "plist not found" in warning


def test_resolve_engine_python_bin_falls_back_when_exe_absent_from_plist_path(tmp_path):
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    plist = _write_plist(tmp_path / "fake.plist", str(empty_bin))

    resolved, warning = _resolve_engine_python_bin("python3", plist_path=plist)
    assert resolved == sys.executable
    assert warning is not None
    assert "not found on the engine's plist PATH" in warning


def test_resolve_engine_python_bin_falls_back_on_malformed_plist(tmp_path):
    bad_plist = tmp_path / "bad.plist"
    bad_plist.write_bytes(b"not a plist at all")

    resolved, warning = _resolve_engine_python_bin("python3", plist_path=bad_plist)
    assert resolved == sys.executable
    assert warning is not None


def _broken_python3(dir_path: Path) -> Path:
    """A 'python3' shaped like the CommandLineTools interpreter that caused the
    2026-07-20/26 outage: importable name, no 'requests', exit 2."""
    dir_path.mkdir(parents=True, exist_ok=True)
    shim = dir_path / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        "echo \"Error: Module 'requests' not found under interpreter /usr/bin/python3\"\n"
        "exit 2\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def test_fallback_runs_doctors_interpreter_not_whatever_python3_is_on_path(tmp_path, monkeypatch):
    # The regression: with plist resolution unavailable, the canary used to spawn
    # a BARE 'python3' resolved from doctor's own PATH. On a host whose PATH
    # leads with an interpreter lacking 'requests', the canary then reported the
    # gate broken (exit 2) when the only thing actually broken was the PATH it
    # invented. Fall back to the interpreter doctor is demonstrably running.
    shim = _broken_python3(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")

    rows = _run_audit_gate_canary_checks(plist_path=tmp_path / "does_not_exist.plist")

    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"          # parity with the engine stays unproved
    assert sys.executable in rows[0]["detail"]
    assert str(shim) not in rows[0]["detail"]
    # The shim's own stdout, which appears only if the shim actually ran. Asserting
    # "exited 2" is absent instead would assert the gate STARTS, which is a
    # different claim and one the flat export tree legitimately cannot make.
    assert "Module 'requests'" not in rows[0]["detail"]


def test_fallback_failure_reports_both_the_uncertainty_and_the_gate_diagnosis(tmp_path, monkeypatch):
    # Two independent facts, and the second must not swallow the first: the
    # engine's interpreter was never confirmed AND the gate did not start. A
    # detail carrying only the exit-2 diagnosis reads as "the gate is broken",
    # hiding that the run never tested the engine's interpreter at all.
    shim = _broken_python3(tmp_path / "bin")
    monkeypatch.setattr(sys, "executable", str(shim))

    rows = _run_audit_gate_canary_checks(plist_path=tmp_path / "does_not_exist.plist")

    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    detail = rows[0]["detail"]
    assert "plist not found" in detail                  # fact 1: resolution failed
    assert "exited 2" in detail                         # fact 2: the gate failed
    assert "Module 'requests'" in detail


def test_kill_switch_selects_doctors_interpreter_not_a_path_python3(tmp_path, monkeypatch):
    # AUDIT_CANARY_RESOLVE_VIA_PLIST=false skips plist resolution, but "skip the
    # plist" must not mean "go back to guessing from PATH".
    shim = _broken_python3(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AUDIT_CANARY_RESOLVE_VIA_PLIST", "false")

    rows = _run_audit_gate_canary_checks(plist_path=tmp_path / "does_not_exist.plist")

    assert len(rows) == 1
    assert sys.executable in rows[0]["detail"]
    assert str(shim) not in rows[0]["detail"]


def test_explicit_python_bin_skips_plist_resolution_even_with_bad_plist_path(tmp_path):
    # Passing python_bin explicitly (as most tests in this file do) must skip
    # plist resolution entirely, per the docstring contract -- so an
    # otherwise-fatal plist_path has no effect and the canary reports plain
    # OK on a genuinely working interpreter.
    rows = _run_audit_gate_canary_checks(
        python_bin=sys.executable, plist_path=tmp_path / "does_not_exist.plist"
    )
    assert rows[0]["status"] == "OK"


def test_canary_actually_uses_plist_path_and_warns_when_it_cannot(tmp_path, monkeypatch):
    # Exercise the real python_bin=None path with an injected plist_path that
    # doesn't exist: resolution must fall back AND the canary must still run
    # (using doctor's own python3) but report WARN, not OK, because the
    # engine's interpreter was never actually confirmed.
    monkeypatch.setenv("AUDIT_CANARY_RESOLVE_VIA_PLIST", "true")
    rows = _run_audit_gate_canary_checks(plist_path=tmp_path / "does_not_exist.plist")
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "could not confirm this is the engine's actual interpreter" in rows[0]["detail"]


@pytest.mark.live_machine
def test_plist_kill_switch_reverts_to_doctors_own_path(monkeypatch, tmp_path):
    # AUDIT_CANARY_RESOLVE_VIA_PLIST=false must skip plist resolution
    # entirely (the pre-fix behavior) even with a broken plist_path injected
    # -- proves the switch actually gates the new code path.
    monkeypatch.setenv("AUDIT_CANARY_RESOLVE_VIA_PLIST", "false")
    rows = _run_audit_gate_canary_checks(plist_path=tmp_path / "does_not_exist.plist")
    assert len(rows) == 1
    # No resolve_warning possible on this path, so OK if the gate is live --
    # matches test_canary_is_green_live's own live-host assumption.
    assert rows[0]["status"] == "OK", rows[0]["detail"]


@pytest.mark.live_machine
def test_real_plist_resolves_to_an_interpreter_with_requests_if_present():
    # If this host actually has the plist (this dev machine does), confirm
    # end-to-end that resolving through it lands on an interpreter that can
    # import 'requests' -- i.e. the canary's OK claim is meaningful, not just
    # "some python3 ran".
    #
    # live_machine (2026-08-15): the early-return guard only covers a host with
    # NO plist. A host that HAS one whose PATH is stale asserts against real
    # local launchd state, which is the definition of a live-machine claim --
    # and is what made this test the non-live profile's one red row at the
    # 2026-08-15 baseline. Nothing is weakened: doctor.py runs this resolution
    # on every real doctor run, and the live profile still asserts it.
    import subprocess

    if not _REAL_PLIST.exists():
        return
    resolved, warning = _resolve_engine_python_bin("python3")
    assert warning is None, warning
    proc = subprocess.run(
        [resolved, "-c", "import requests"], capture_output=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
