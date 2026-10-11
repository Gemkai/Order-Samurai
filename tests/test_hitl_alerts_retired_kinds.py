"""Retirement violations must remain visible in the HITL digest."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ORDER_SAMURAI = Path(__file__).resolve().parents[1]
try:
    _spec = importlib.util.spec_from_file_location(
        "hitl_alerts_retired_contract", ORDER_SAMURAI / "bin" / "hitl_alerts.py",
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("hitl_alerts loader unavailable")
    hitl_alerts = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = hitl_alerts
    _spec.loader.exec_module(hitl_alerts)
except ImportError:
    hitl_alerts = None


def test_digest_result_kinds_include_retirement_findings_and_keep_existing_members():
    assert hitl_alerts is not None, "hitl_alerts module unavailable"
    existing = {
        "stale", "failed", "blocked", "interrupted", "auth-failure", "push-failure", "missing-result",
    }
    retired = {"retired-still-loaded", "retired-not-disabled", "retired-unverified"}
    assert existing <= hitl_alerts.DIGEST_RESULT_KINDS
    assert retired <= hitl_alerts.DIGEST_RESULT_KINDS
