"""Doctor's injection-hook canary (execution/doctor.py::_run_injection_hook_checks).

The read-injection-scanner hook's real pytest suite is live_machine (deselected
in CI, run only by manual verify.sh), so this doctor probe is the ONLY scheduled
thing that notices the hook vanishing or breaking. These tests pin the probe's
own contract with injectable paths — no dependency on the real ~/.claude, so
they run everywhere (CI included), unlike the suite they backstop.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution.doctor import _run_injection_hook_checks  # noqa: E402

NODE = shutil.which("node")


def _one(results: list[dict]) -> dict:
    assert len(results) == 1
    return results[0]


def test_missing_hook_file_warns(tmp_path):
    row = _one(_run_injection_hook_checks(hook_path=tmp_path / "nope.js"))
    assert row["status"] == "WARN"
    assert "missing" in row["detail"]
    # The WARN must say what is no longer protected, not just what file is absent.
    assert "NOT being scanned" in row["detail"]


def test_missing_node_warns(tmp_path, monkeypatch):
    hook = tmp_path / "hook.js"
    hook.write_text("process.exit(0);\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    row = _one(_run_injection_hook_checks(hook_path=hook))
    assert row["status"] == "WARN"
    assert "node is not on PATH" in row["detail"]


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_healthy_hook_is_ok(tmp_path):
    hook = tmp_path / "hook.js"
    # Minimal stand-in honoring the real hook's contract: read stdin, exit 0.
    hook.write_text(
        "process.stdin.resume(); process.stdin.on('end', () => process.exit(0));\n",
        encoding="utf-8")
    row = _one(_run_injection_hook_checks(hook_path=hook, node_bin=NODE))
    assert row["status"] == "OK"


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_hook_that_exits_nonzero_on_benign_input_warns(tmp_path):
    hook = tmp_path / "hook.js"
    hook.write_text("process.exit(2);\n", encoding="utf-8")
    row = _one(_run_injection_hook_checks(hook_path=hook, node_bin=NODE))
    assert row["status"] == "WARN"
    assert "exited 2" in row["detail"]
    # Must explain why nonzero is fatal for a hook, not just report the code.
    assert "never-block" in row["detail"]


def test_unexecutable_node_binary_warns(tmp_path):
    hook = tmp_path / "hook.js"
    hook.write_text("process.exit(0);\n", encoding="utf-8")
    fake_node = tmp_path / "not-really-node"
    fake_node.write_text("", encoding="utf-8")
    # not executable → PermissionError (or FileNotFoundError on some platforms)
    os.chmod(fake_node, stat.S_IRUSR)
    row = _one(_run_injection_hook_checks(hook_path=hook, node_bin=str(fake_node)))
    assert row["status"] == "WARN"
    assert "failed to execute" in row["detail"]
