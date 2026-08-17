"""Import-failure diagnosis in execution/audit_remediation_patch.py.

The 2026-07-20/26 'audit_rejected' failures (exit 2) were a missing TRANSITIVE
module — 'requests', imported by agentica_core.llm.gateway — under the
CommandLineTools system python the reflex engine's bare-'python3' spawn
resolved. The old blanket ImportError message blamed GOVERNANCE_ROOT, sending
the diagnosis in the wrong direction. These tests pin the fixed contract: the
message names the missing module and the interpreter, and the exit-2
fail-closed semantics are unchanged.

The failing import happens at module import time, so an in-process import
would kill pytest collection — every test runs the script as a subprocess,
exactly as the reflex engine does.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from _layout import governance_root

_SCRIPT = Path(__file__).resolve().parents[1] / "execution" / "audit_remediation_patch.py"

# Mirrors Governance/api/src/reflex-engine.ts:330 exactly (that file is a
# sibling group's; this is a read-only copy for cross-checking, not a second
# source of truth). The engine tests this against `${stderr}\n${stdout}` --
# see reflex-engine.ts:2783 -- to classify an audit failure as an env error
# (audit_env_error) vs. a genuine security veto (audit_rejected). A message
# that doesn't match either regex would be silently misclassified as the
# latter, which is the exact misdiagnosis this fix group exists to eliminate.
_AUDIT_ENV_ERROR_RE = re.compile(r"Cannot import agentica_core|ModuleNotFoundError|ImportError")


def _run_with_gov_root(gov_root: Path, patch: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "GOVERNANCE_ROOT": str(gov_root)}
    # PYTHONPATH could resurrect the real agentica_core and mask the failure
    # these tests deliberately induce.
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--patch", str(patch)],
        capture_output=True, text=True, timeout=60, env=env,
    )


def _empty_patch(tmp_path: Path) -> Path:
    patch = tmp_path / "benign.patch"
    patch.write_text("", encoding="utf-8")
    return patch


def _unresolvable_agentica_core(tmp_path: Path) -> Path:
    """A GOVERNANCE_ROOT where `agentica_core.llm.gateway` cannot resolve.

    An EMPTY dir is not enough. The script inserts GOVERNANCE_ROOT at sys.path[0],
    so an empty root only removes the source-tree copy — in an INSTALLED
    distribution `agentica_core` is a shipped package and site-packages answers
    the import anyway, the gate starts, and the empty patch is approved (exit 0).
    That is correct product behaviour and a false test failure: the suite ships
    inside the distribution, so it must induce the condition rather than assume
    the environment lacks the package.

    Writing a stub package here shadows site-packages from sys.path[0] while
    genuinely lacking the import target, so the script takes the very same
    `except ModuleNotFoundError` branch, under both layouts.
    """
    (tmp_path / "agentica_core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "agentica_core" / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


def test_missing_package_exits_2_naming_module_and_interpreter(tmp_path):
    # GOVERNANCE_ROOT where agentica_core cannot be resolved: the message must
    # name the unresolvable module and the interpreter, not just wave at
    # GOVERNANCE_ROOT.
    root = _unresolvable_agentica_core(tmp_path)
    proc = _run_with_gov_root(root, _empty_patch(tmp_path))
    assert proc.returncode == 2
    out = proc.stdout + proc.stderr
    assert "agentica_core" in out
    assert sys.executable in out


def test_missing_transitive_module_exits_2_naming_the_real_culprit(tmp_path):
    # The exact 2026-07-20/26 shape: agentica_core IS importable, but gateway's
    # own import chain hits a module the interpreter lacks. exc.name must
    # surface the transitive culprit, not 'agentica_core'.
    pkg = tmp_path / "agentica_core" / "llm"
    pkg.mkdir(parents=True)
    (tmp_path / "agentica_core" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "gateway.py").write_text(
        "import canary_module_missing_on_purpose_xyz\ngateway = None\n",
        encoding="utf-8",
    )
    proc = _run_with_gov_root(tmp_path, _empty_patch(tmp_path))
    assert proc.returncode == 2
    out = proc.stdout + proc.stderr
    assert "canary_module_missing_on_purpose_xyz" in out
    assert sys.executable in out


def test_missing_package_output_matches_engine_env_error_classifier(tmp_path):
    # The regression this fix group exists to close: the message must contain
    # a token the ENGINE's classifier actually looks for, not just SOME
    # module/interpreter names that read well to a human. If this test passes
    # while reflex-engine.ts's regex changes shape, the two sides have drifted
    # again and exec_log would silently mislabel audit_env_error as
    # audit_rejected exactly like the 2026-07-20/26 outage.
    root = _unresolvable_agentica_core(tmp_path)
    proc = _run_with_gov_root(root, _empty_patch(tmp_path))
    assert proc.returncode == 2
    out = proc.stdout + "\n" + proc.stderr
    assert _AUDIT_ENV_ERROR_RE.search(out), (
        f"script output does not match the engine's AUDIT_ENV_ERROR_RE "
        f"classifier -- would be misclassified as audit_rejected: {out!r}"
    )


def test_missing_transitive_module_output_matches_engine_env_error_classifier(tmp_path):
    # Same contract, but for the DOMINANT real-world shape: agentica_core
    # imports fine, a transitive dependency (e.g. 'requests') doesn't.
    pkg = tmp_path / "agentica_core" / "llm"
    pkg.mkdir(parents=True)
    (tmp_path / "agentica_core" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "gateway.py").write_text(
        "import canary_module_missing_on_purpose_xyz\ngateway = None\n",
        encoding="utf-8",
    )
    proc = _run_with_gov_root(tmp_path, _empty_patch(tmp_path))
    assert proc.returncode == 2
    out = proc.stdout + "\n" + proc.stderr
    assert _AUDIT_ENV_ERROR_RE.search(out), (
        f"script output does not match the engine's AUDIT_ENV_ERROR_RE "
        f"classifier -- would be misclassified as audit_rejected: {out!r}"
    )


def test_benign_empty_patch_exits_0_under_current_interpreter(tmp_path):
    # The full import chain (gateway -> requests) plus the approve-by-default
    # empty-patch path, under the interpreter running this suite. No LLM call.
    proc = _run_with_gov_root(governance_root(__file__), _empty_patch(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Approving by default" in proc.stdout
