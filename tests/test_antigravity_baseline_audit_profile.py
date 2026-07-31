"""A clean Antigravity install must produce zero FAILs.

Asserts baseline profile behavior for Antigravity runtime checks on a stock setup.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentica_core.providers.antigravity_verifiers import run_checks


@pytest.fixture
def fresh_antigravity_install(tmp_path: Path) -> Path:
    root = tmp_path / "fresh-antigravity"
    root.mkdir()
    (root / "telemetry").mkdir()
    (root / "telemetry" / "telemetry.jsonl").write_text("", encoding="utf-8")
    return root


def test_clean_antigravity_install_zero_fails(fresh_antigravity_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SAMURAI_AUDIT_PROFILE", "baseline")

    class DummyPlatform:
        runtime_root = fresh_antigravity_install
        surface_matrix = Path(__file__).resolve().parents[1] / "platform_surfaces" / "antigravity_surface_matrix.json"
        telemetry_source = fresh_antigravity_install / "telemetry" / "telemetry.jsonl"

    monkeypatch.setattr("agentica_core.providers.antigravity_verifiers.resolve_platform", lambda _: DummyPlatform())
    results = run_checks()
    fails = [r for r in results if r["status"] == "FAIL"]
    assert not fails, f"expected zero FAILs on baseline antigravity install, got {fails}"


def test_full_profile_antigravity_asserts_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SAMURAI_AUDIT_PROFILE", "full")
    missing_root = tmp_path / "missing-antigravity"

    class DummyPlatform:
        runtime_root = missing_root
        surface_matrix = Path(__file__).resolve().parents[1] / "platform_surfaces" / "antigravity_surface_matrix.json"
        telemetry_source = missing_root / "telemetry" / "telemetry.jsonl"

    monkeypatch.setattr("agentica_core.providers.antigravity_verifiers.resolve_platform", lambda _: DummyPlatform())
    results = run_checks()
    fails = [r for r in results if r["status"] == "FAIL"]
    assert len(fails) > 0, "full profile should fail missing runtime root"
