"""Focused tests for doctor's tamper-evident exec-log consumer."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution import doctor  # noqa: E402


def _api(tmp_path: Path, *, with_tsx: bool = True) -> tuple[Path, Path]:
    api = tmp_path / "api"
    source = api / "src" / "verify-chain-cli.ts"
    source.parent.mkdir(parents=True)
    source.write_text("// fixture\n", encoding="utf-8")
    tsx = api / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    if with_tsx:
        tsx.parent.mkdir(parents=True)
        tsx.write_text("#!/bin/sh\n", encoding="utf-8")
    return api, tsx


def _must_not_run(*_args, **_kwargs):
    raise AssertionError("a dependency-cold checkout must not start a subprocess")


def test_missing_local_tsx_warns_without_invoking_package_resolution(tmp_path: Path):
    api, tsx = _api(tmp_path, with_tsx=False)

    rows = doctor._run_exec_chain_checks(api_dir=api, runner=_must_not_run)

    assert rows == [{
        "status": "WARN",
        "label": "exec-chain",
        "detail": (
            f"local tsx runtime missing at {tsx} -- exec_log tamper-evidence is "
            "unverified (install Governance/api dependencies, then re-run doctor)"
        ),
    }]


def test_ready_checkout_invokes_only_the_repository_pinned_tsx(tmp_path: Path):
    api, tsx = _api(tmp_path)
    seen = {}

    def run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            stdout='{"ok":true,"chained":7,"unchained":3}\n',
            stderr="",
            returncode=0,
        )

    rows = doctor._run_exec_chain_checks(api_dir=api, runner=run)

    assert seen["command"] == [str(tsx), "src/verify-chain-cli.ts"]
    assert seen["kwargs"]["cwd"] == str(api)
    assert seen["kwargs"]["timeout"] == 60
    assert rows == [{
        "status": "OK",
        "label": "exec-chain",
        "detail": "exec_log hash chain intact (7 chained row(s) verified, 3 pre-migration)",
    }]


def test_broken_chain_remains_a_gating_failure(tmp_path: Path):
    api, _ = _api(tmp_path)

    def run(*_args, **_kwargs):
        return SimpleNamespace(
            stdout=(
                '{"ok":false,"chained":4,"unchained":8,"brokenAtSeq":5,'
                '"reason":"content tampered"}\n'
            ),
            stderr="",
            returncode=1,
        )

    rows = doctor._run_exec_chain_checks(api_dir=api, runner=run)

    assert rows[0]["status"] == "FAIL"
    assert "BROKEN at seq 5" in rows[0]["detail"]
    assert "content tampered" in rows[0]["detail"]


def test_local_tsx_that_vanishes_before_spawn_warns(tmp_path: Path):
    api, tsx = _api(tmp_path)

    def run(*_args, **_kwargs):
        raise FileNotFoundError

    rows = doctor._run_exec_chain_checks(api_dir=api, runner=run)

    assert rows == [{
        "status": "WARN",
        "label": "exec-chain",
        "detail": (
            f"local tsx runtime could not start at {tsx} -- exec_log tamper-evidence is unverified"
        ),
    }]


def test_ready_checkout_timeout_still_reports_unverified_chain(tmp_path: Path):
    api, _ = _api(tmp_path)

    def run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="tsx", timeout=60)

    rows = doctor._run_exec_chain_checks(api_dir=api, runner=run)

    assert rows == [{
        "status": "WARN",
        "label": "exec-chain",
        "detail": (
            "chain verification timed out after 60s -- exec_log tamper-evidence is unverified"
        ),
    }]
