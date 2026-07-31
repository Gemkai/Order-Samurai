"""A clean Gemini install must produce zero FAILs.

Asserts baseline profile behavior for Gemini CLI runtime checks on a stock setup.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentica_core.providers.gemini_verifiers import run_checks


@pytest.fixture
def fresh_gemini_install(tmp_path: Path) -> Path:
    root = tmp_path / "fresh-gemini"
    root.mkdir()
    (root / "telemetry").mkdir()
    (root / "telemetry" / "telemetry.jsonl").write_text("", encoding="utf-8")
    return root


def test_clean_gemini_install_zero_fails(fresh_gemini_install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SAMURAI_AUDIT_PROFILE", "baseline")

    class DummyPlatform:
        runtime_root = fresh_gemini_install
        surface_matrix = Path(__file__).resolve().parents[1] / "platform_surfaces" / "gemini_surface_matrix.json"
        telemetry_source = fresh_gemini_install / "telemetry" / "telemetry.jsonl"

    monkeypatch.setattr("agentica_core.providers.gemini_verifiers.resolve_platform", lambda _: DummyPlatform())
    results = run_checks()
    fails = [r for r in results if r["status"] == "FAIL"]
    assert not fails, f"expected zero FAILs on baseline gemini install, got {fails}"


def test_full_profile_gemini_asserts_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORDER_SAMURAI_AUDIT_PROFILE", "full")
    missing_root = tmp_path / "missing-gemini"

    class DummyPlatform:
        runtime_root = missing_root
        surface_matrix = Path(__file__).resolve().parents[1] / "platform_surfaces" / "gemini_surface_matrix.json"
        telemetry_source = missing_root / "telemetry" / "telemetry.jsonl"

    monkeypatch.setattr("agentica_core.providers.gemini_verifiers.resolve_platform", lambda _: DummyPlatform())
    results = run_checks()
    fails = [r for r in results if r["status"] == "FAIL"]
    assert len(fails) > 0, "full profile should fail missing runtime root"
