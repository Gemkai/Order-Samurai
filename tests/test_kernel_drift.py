"""Drift tripwire between the two agentica_core kernels.

The repo-local kernel (this repo) is FROZEN; the Agentica OS Governance kernel
is the active one. Shared functions must stay semantically identical or
calibration/time-parsing silently diverges between the dashboard and the hub.

Compares normalized AST dumps of the shared functions. SKIPs (with a warning)
when the Governance kernel is not present on this machine.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

LOCAL_KERNEL = Path(__file__).resolve().parents[1] / "agentica_core" / "aggregate.py"
GOVERNANCE_KERNEL = Path(os.environ.get(
    "AGENTICA_GOVERNANCE", r"C:\Users\jemak\Desktop\Agentica OS\Governance"
)) / "agentica_core" / "aggregate.py"

SHARED_FUNCTIONS = ["_parse_iso", "_calibrate_coefficients"]


def _extract_function(source_path: Path, name: str) -> str | None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            # Strip the docstring so comment-level edits don't trip the wire
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
            return ast.dump(node, annotate_fields=False, include_attributes=False)
    return None


@pytest.mark.parametrize("func_name", SHARED_FUNCTIONS)
def test_shared_function_has_not_drifted(func_name):
    if not GOVERNANCE_KERNEL.exists():
        pytest.skip(f"Governance kernel not found at {GOVERNANCE_KERNEL} "
                    f"(set AGENTICA_GOVERNANCE) — drift not checkable here")
    local = _extract_function(LOCAL_KERNEL, func_name)
    governance = _extract_function(GOVERNANCE_KERNEL, func_name)
    if local is None or governance is None:
        pytest.skip(f"{func_name} missing in one kernel "
                    f"(local={local is not None}, governance={governance is not None}) "
                    f"— shared-function set needs updating, not comparable")
    assert local == governance, (
        f"{func_name} has drifted between the frozen Order Samurai kernel and the "
        f"Governance kernel. Sync the implementations (Governance copy is canonical) "
        f"or consciously update SHARED_FUNCTIONS."
    )
