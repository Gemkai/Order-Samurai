"""Visible degradation when optional out-of-package helpers are unavailable."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ORDER_SAMURAI = Path(__file__).resolve().parents[1]
if str(ORDER_SAMURAI) not in sys.path:
    sys.path.insert(0, str(ORDER_SAMURAI))

from execution.doctor import _run_claude_hook_health_checks
from execution.verify_falsifiability import check_pii_export, run_falsifiability

_SPEC = importlib.util.spec_from_file_location(
    "hitl_alerts_visibility", ORDER_SAMURAI / "bin" / "hitl_alerts.py"
)
assert _SPEC and _SPEC.loader
hitl_alerts = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = hitl_alerts
_SPEC.loader.exec_module(hitl_alerts)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
SENTINEL_KEY = "test-only-secret-must-not-appear-in-warnings"


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch, tmp_path):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_RUNTIME_ROOT", str(tmp_path / ".claude"))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setitem(
        sys.modules, "secret_env", SimpleNamespace(lookup=lambda name: None)
    )
    monkeypatch.setitem(sys.modules, "hook_registry", None)


@pytest.fixture
def hook_files(tmp_path):
    quarantine = tmp_path / "hook_quarantine.json"
    quarantine.write_text(json.dumps({
        "quarantined": {"h1": {
            "quarantined_at": int((NOW - timedelta(hours=1)).timestamp()),
            "consecutive_failures": 3,
        }},
    }), encoding="utf-8")
    timings = tmp_path / "hook_timings.jsonl"
    timings.write_text(json.dumps({
        "ts": (NOW - timedelta(minutes=1)).isoformat(),
        "hook": "healthy-hook",
        "status": "ok",
    }) + "\n", encoding="utf-8")
    return {"quarantine_path": quarantine, "timings_path": timings, "now": NOW}


def test_resend_missing_helper_warns_once(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "secret_env", None)

    assert hitl_alerts._resend_key() == ""

    _assert_resend_warning(capsys.readouterr().err, ("ModuleNotFoundError", "ImportError"))


def test_resend_lookup_failure_warns_without_leaking_key(monkeypatch, capsys):
    calls = []

    def lookup(name):
        calls.append(name)
        raise RuntimeError(f"lookup failed with {SENTINEL_KEY}")

    monkeypatch.setitem(sys.modules, "secret_env", SimpleNamespace(lookup=lookup))

    assert hitl_alerts._resend_key() == ""
    assert calls == ["RESEND_API_KEY"]
    stderr = capsys.readouterr().err
    assert SENTINEL_KEY not in stderr
    _assert_resend_warning(stderr, ("RuntimeError",))


def test_resend_missing_stored_key_is_silent(monkeypatch, capsys):
    calls = []

    def lookup(name):
        calls.append(name)
        return None

    monkeypatch.setitem(sys.modules, "secret_env", SimpleNamespace(lookup=lookup))

    assert hitl_alerts._resend_key() == ""
    assert calls == ["RESEND_API_KEY"]
    assert capsys.readouterr().err == ""


def test_resend_env_key_is_returned_silently(monkeypatch, capsys):
    monkeypatch.setenv("RESEND_API_KEY", SENTINEL_KEY)
    monkeypatch.setitem(sys.modules, "secret_env", None)

    assert hitl_alerts._resend_key() == SENTINEL_KEY
    assert capsys.readouterr().err == ""


def test_hook_registry_import_failure_is_visible_in_each_hook_row(hook_files):
    rows = _run_claude_hook_health_checks(registry=None, **hook_files)
    hook_rows = [row for row in rows if row["label"] == "claude-hook-health.h1"]

    assert len(hook_rows) == 1
    for row in hook_rows:
        assert row["status"] == "WARN"
        assert "hook_registry unavailable" in row["detail"]
        assert any(name in row["detail"] for name in ("ModuleNotFoundError", "ImportError"))


def test_hook_registry_import_failure_is_visible_when_telemetry_missing(hook_files):
    hook_files["timings_path"].unlink()

    rows = _run_claude_hook_health_checks(registry=None, **hook_files)

    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "no hook telemetry" in rows[0]["detail"]
    assert "hook_registry unavailable" in rows[0]["detail"]
    assert "ModuleNotFoundError" in rows[0]["detail"]


def test_hook_registry_import_failure_is_visible_when_quarantine_unreadable(hook_files):
    hook_files["quarantine_path"].write_text("{", encoding="utf-8")

    rows = _run_claude_hook_health_checks(registry=None, **hook_files)

    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "quarantine state cannot be measured" in rows[0]["detail"]
    assert "hook_registry unavailable" in rows[0]["detail"]
    assert "ModuleNotFoundError" in rows[0]["detail"]


def test_hook_registry_import_failure_is_visible_when_nothing_quarantined(hook_files):
    hook_files["quarantine_path"].write_text(
        json.dumps({"quarantined": {}}), encoding="utf-8"
    )

    rows = _run_claude_hook_health_checks(registry=None, **hook_files)

    assert len(rows) == 1
    assert rows[0]["status"] == "OK"
    assert "0 hooks quarantined; 1 dispatches" in rows[0]["detail"]
    assert "hook_registry unavailable" in rows[0]["detail"]
    assert "ModuleNotFoundError" in rows[0]["detail"]


def test_hook_registry_import_failure_is_visible_when_telemetry_unreadable(
    hook_files, monkeypatch
):
    original_open = Path.open

    def open_with_unreadable_telemetry(path, *args, **kwargs):
        if path == hook_files["timings_path"]:
            raise PermissionError("test telemetry read denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_with_unreadable_telemetry)

    rows = _run_claude_hook_health_checks(registry=None, **hook_files)

    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert "test telemetry read denied" in rows[0]["detail"]
    assert "quarantine impact cannot be measured" in rows[0]["detail"]
    assert "hook_registry unavailable" in rows[0]["detail"]
    assert "ModuleNotFoundError" in rows[0]["detail"]


@pytest.mark.parametrize(
    ("fail_share", "expected_status"),
    [(0.75, "WARN"), (0.5, "WARN"), (0.25, "FAIL")],
    ids=["below-threshold", "at-threshold", "above-threshold"],
)
def test_hook_registry_import_failure_is_visible_in_share_row(
    hook_files, fail_share, expected_status
):
    with hook_files["timings_path"].open("a", encoding="utf-8") as timings:
        timings.write(json.dumps({
            "ts": (NOW - timedelta(minutes=1)).isoformat(),
            "hook": "h1",
            "status": "quarantined",
        }) + "\n")

    rows = _run_claude_hook_health_checks(
        registry=None, fail_share=fail_share, **hook_files
    )
    share_rows = [row for row in rows if row["label"] == "claude-hook-health.share"]

    assert len(share_rows) == 1
    assert share_rows[0]["status"] == expected_status
    assert "1 of 2 dispatches" in share_rows[0]["detail"]
    assert "hook_registry unavailable" in share_rows[0]["detail"]
    assert "ModuleNotFoundError" in share_rows[0]["detail"]


@pytest.mark.parametrize(
    ("telemetry_missing", "expected_status", "fail_share"),
    [(True, "WARN", None), (False, "OK", None),
     (False, "WARN", 0.5), (False, "FAIL", 0.25)],
    ids=["telemetry-missing", "nothing-quarantined", "share-warn", "share-fail"],
)
def test_explicit_registry_has_no_unavailable_detail(
    hook_files, telemetry_missing, expected_status, fail_share
):
    if fail_share is None:
        hook_files["quarantine_path"].write_text(
            json.dumps({"quarantined": {}}), encoding="utf-8"
        )
    else:
        with hook_files["timings_path"].open("a", encoding="utf-8") as timings:
            timings.write(json.dumps({
                "ts": (NOW - timedelta(minutes=1)).isoformat(),
                "hook": "h1",
                "status": "quarantined",
            }) + "\n")
    if telemetry_missing:
        hook_files["timings_path"].unlink()

    rows = _run_claude_hook_health_checks(
        registry={}, fail_share=fail_share if fail_share is not None else 0.05,
        **hook_files,
    )

    if fail_share is not None:
        assert all("hook_registry unavailable" not in row["detail"] for row in rows)
        rows = [row for row in rows if row["label"] == "claude-hook-health.share"]

    assert len(rows) == 1
    assert rows[0]["status"] == expected_status
    assert "hook_registry unavailable" not in rows[0]["detail"]


def test_explicit_blocking_registry_fails_without_unavailable_detail(hook_files):
    rows = _run_claude_hook_health_checks(
        registry={"h1": {"criticality": "blocking"}}, **hook_files
    )
    hook_rows = [row for row in rows if row["label"] == "claude-hook-health.h1"]

    assert len(hook_rows) == 1
    assert hook_rows[0]["status"] == "FAIL"
    assert "hook_registry unavailable" not in hook_rows[0]["detail"]


def test_pii_export_import_failure_remains_a_visible_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "extract_public", None)
    for kind in ("bad", "clean"):
        (tmp_path / "pii_export" / kind).mkdir(parents=True)

    results = run_falsifiability(
        checks={"pii_export": check_pii_export}, fixtures_root=tmp_path,
        verify_scripts=[],
    )

    result = results["checks"]["pii_export"]
    assert result["status"] == "error"
    assert "extract_public" in result["detail"]
    assert any(name in result["detail"] for name in ("ModuleNotFoundError", "ImportError"))


def _assert_resend_warning(stderr, exception_names):
    lines = stderr.splitlines()
    assert len(lines) == 1, f"expected one stderr WARN line, got {stderr!r}"
    assert lines[0].startswith("WARN")
    assert "RESEND_API_KEY" in lines[0]
    assert "Mail.app" in lines[0]
    assert any(name in lines[0] for name in exception_names)
