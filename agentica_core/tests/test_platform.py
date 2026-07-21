import pytest

from agentica_core import (
    AmbiguousPlatform,
    PlatformUnavailable,
    list_platforms,
    resolve_platform,
)
import agentica_core.adapter as plat


def test_registry_lists_supported_platforms():
    names = list_platforms()
    assert "claude" in names
    assert "codex" in names


def test_resolve_claude_real_machine():
    a = resolve_platform("claude")
    assert a.name == "claude"
    assert a.runtime_root.name == ".claude"
    assert a.available()
    assert a.surface_matrix.name == "claude_surface_matrix.json"


def test_four_slots_present():
    a = resolve_platform("claude")
    assert a.runtime_root is not None        # slot A
    assert a.telemetry_source is not None     # slot B
    assert a.verifiers == ()                   # slot C — populated in a later phase
    assert a.surface_matrix is not None        # slot D


def test_codex_surface_matrix_is_tracked_in_governance():
    a = resolve_platform("codex")
    assert a.name == "codex"
    assert a.surface_matrix.name == "codex_surface_matrix.json"
    assert "Governance" in str(a.surface_matrix) or "platform_surfaces" in str(a.surface_matrix)
    assert a.surface_matrix.exists()


def test_unknown_platform_raises():
    with pytest.raises(PlatformUnavailable):
        resolve_platform("nonsense-platform")


def test_missing_root_fails_loud_no_fallback(monkeypatch):
    monkeypatch.setattr(plat, "_load_registry", lambda: {
        "ghost": {
            "runtime_root": "~/.this-root-does-not-exist-xyz",
            "telemetry_source": "~/.this-root-does-not-exist-xyz/t.jsonl",
            "surface_matrix": "~/.this-root-does-not-exist-xyz/s.json",
        }
    })
    with pytest.raises(PlatformUnavailable):
        resolve_platform("ghost")  # must raise, never substitute a local copy


def test_autodetect_single(monkeypatch, tmp_path):
    monkeypatch.setattr(plat, "_load_registry", lambda: {
        "only": {
            "runtime_root": str(tmp_path),
            "telemetry_source": str(tmp_path / "t.jsonl"),
            "surface_matrix": str(tmp_path / "s.json"),
        }
    })
    a = resolve_platform()
    assert a.name == "only"


def test_autodetect_ambiguous(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(plat, "_load_registry", lambda: {
        "a": {"runtime_root": str(tmp_path), "telemetry_source": "x", "surface_matrix": "y"},
        "b": {"runtime_root": str(other), "telemetry_source": "x", "surface_matrix": "y"},
    })
    with pytest.raises(AmbiguousPlatform):
        resolve_platform()


def test_autodetect_none(monkeypatch):
    monkeypatch.setattr(plat, "_load_registry", lambda: {
        "ghost": {"runtime_root": "~/.nope-xyz", "telemetry_source": "x", "surface_matrix": "y"},
    })
    with pytest.raises(PlatformUnavailable):
        resolve_platform()
