"""Tests for bin/samurai's `doctor` Path Authority check.

Check 5 looked for `agentica_core` as a direct CHILD of `paths["root"]`
(Order Samurai's own directory). But in the real, live-tree layout,
`agentica_core` lives one level up under `Governance/` -- a SIBLING of
Order Samurai, not a child -- exactly like every other script in bin/
resolves it (emit_event.py, bushido_check.py, secret_scrub.py, ...). The
check only matched the flattened public-export layout, so `samurai doctor`
reported Path Authority FAIL on every real dev checkout.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
_loader = SourceFileLoader("samurai_cli_doctor", str(_SAMURAI_PATH))
_spec = importlib.util.spec_from_loader("samurai_cli_doctor", _loader)
assert _spec
samurai_cli = importlib.util.module_from_spec(_spec)
sys.modules["samurai_cli_doctor"] = samurai_cli
_loader.exec_module(samurai_cli)


def _fake_paths(tmp_path: Path, root: Path) -> dict:
    samurai_home = tmp_path / ".samurai"
    return {
        "root": root,
        "home": samurai_home,
        "samurai_settings": samurai_home / "settings.json",
        "claude_hooks": tmp_path / ".claude" / "hooks",
        "claude_settings": tmp_path / ".claude" / "hooks" / "settings.json",
        "backups": samurai_home / "backups",
        "state": root / "state",
        "taxonomy": root / "state" / "kill_chain_taxonomy.json",
    }


def _run_doctor(monkeypatch, paths: dict) -> str:
    monkeypatch.setattr(samurai_cli, "get_paths", lambda: paths)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        samurai_cli.cmd_doctor(None)
    return buf.getvalue()


def _path_authority_line(output: str) -> str:
    lines = [line for line in output.splitlines() if "Path Authority" in line]
    assert len(lines) == 1
    return lines[0]


def test_path_authority_passes_when_agentica_core_is_a_sibling_of_root(tmp_path, monkeypatch):
    # Live-tree layout: <repo>/Governance/Order Samurai/ (root) with
    # agentica_core as a SIBLING under Governance/, not a child of root.
    governance = tmp_path / "Governance"
    order_samurai_root = governance / "Order Samurai"
    order_samurai_root.mkdir(parents=True)
    (governance / "agentica_core").mkdir()

    output = _run_doctor(monkeypatch, _fake_paths(tmp_path, order_samurai_root))

    line = _path_authority_line(output)
    assert "[✅ PASS]" in line, f"Path Authority failed in the real nested-repo layout: {line}"


def test_path_authority_still_passes_in_the_flattened_export_layout(tmp_path, monkeypatch):
    # Public-export layout: the pack is flattened so agentica_core IS a direct
    # child of root -- the case the check already handled; must not regress.
    root = tmp_path / "order-samurai-export"
    root.mkdir()
    (root / "agentica_core").mkdir()

    output = _run_doctor(monkeypatch, _fake_paths(tmp_path, root))

    line = _path_authority_line(output)
    assert "[✅ PASS]" in line, f"Path Authority regressed in the flattened export layout: {line}"


def test_path_authority_fails_when_agentica_core_is_nowhere_to_be_found(tmp_path, monkeypatch):
    root = tmp_path / "order-samurai-broken"
    root.mkdir()
    # No agentica_core anywhere -- a genuinely broken install.

    output = _run_doctor(monkeypatch, _fake_paths(tmp_path, root))

    line = _path_authority_line(output)
    assert "[❌ FAIL]" in line, f"Path Authority should fail with no agentica_core anywhere: {line}"
