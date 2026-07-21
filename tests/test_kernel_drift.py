"""Drift tripwire between the two agentica_core kernels.

The repo-local kernel (this repo) is FROZEN; the Agentica OS Governance kernel
is the active one. Shared functions must stay semantically identical or
calibration/time-parsing silently diverges between the dashboard and the hub.

Two guards:
  1. Shared-function AST comparison — _parse_iso, _calibrate_coefficients must
     not drift between kernels.
  2. Orphan-metric check — any metric name present in the frozen REGISTRY but
     absent from the live Governance REGISTRY is a vestigial duplicate that can
     mislead reviewers (caught Canary_Health, 2026-06-21).

Both guards SKIP when the Governance kernel is not present on this machine.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

# The frozen ronin metric engine was merged into the canonical Governance kernel
# (parents[2]/agentica_core) as ronin_metrics.py; the live dashboard kernel is
# aggregate.py in the same package. The drift tripwire now compares those two files.
_GOVERNANCE_DIR = Path(__file__).resolve().parents[2]
LOCAL_KERNEL = _GOVERNANCE_DIR / "agentica_core" / "ronin_metrics.py"
GOVERNANCE_KERNEL = Path(os.environ.get(
    "AGENTICA_GOVERNANCE", str(_GOVERNANCE_DIR)
)) / "agentica_core" / "aggregate.py"

SHARED_FUNCTIONS = ["_parse_iso", "_calibrate_coefficients"]

# Both kernels use one of these field names for the metric identifier in REGISTRY dicts.
# Frozen kernel uses "metric"; Governance kernel may use "key".
_METRIC_FIELD_CANDIDATES = ("metric", "key")


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

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


def _metric_keys_from_list(node: ast.expr) -> set[str]:
    """Extract metric name strings from an ast.List of ast.Dict REGISTRY entries."""
    if not isinstance(node, ast.List):
        return set()
    result: set[str] = set()
    for elt in node.elts:
        if not isinstance(elt, ast.Dict):
            continue
        for k, v in zip(elt.keys, elt.values):
            if (isinstance(k, ast.Constant) and k.value in _METRIC_FIELD_CANDIDATES
                    and isinstance(v, ast.Constant)):
                result.add(str(v.value))
    return result


def _extract_metric_keys(source_path: Path) -> set[str]:
    """Walk the module AST and return metric names from the REGISTRY assignment.

    Handles both annotated (REGISTRY: list[dict] = [...]) and plain
    (REGISTRY = [...]) assignment forms.  Returns an empty set when REGISTRY
    cannot be resolved to a static list (e.g. built programmatically).
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "REGISTRY":
                    return _metric_keys_from_list(node.value)
        elif isinstance(node, ast.AnnAssign):
            if (isinstance(node.target, ast.Name) and node.target.id == "REGISTRY"
                    and node.value is not None):
                return _metric_keys_from_list(node.value)
    return set()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

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


def test_frozen_registry_has_no_metric_absent_from_governance():
    """Orphan-metric guard: a metric key in the frozen REGISTRY that is absent from
    the live Governance REGISTRY is a vestigial duplicate — it misleads reviewers
    into thinking the frozen kernel is the source of truth for that metric.

    This guard caught Canary_Health (2026-06-21); that reducer was removed after
    confirmation that no live code consumed it.

    See: docs/solutions/best-practices/canary-failures-vs-gate-canary-fault-two-files-2026-06-20.md
    """
    if not GOVERNANCE_KERNEL.exists():
        pytest.skip(
            f"Governance kernel not found at {GOVERNANCE_KERNEL} "
            f"(set AGENTICA_GOVERNANCE) — orphan-metric check skipped"
        )
    frozen_keys = _extract_metric_keys(LOCAL_KERNEL)
    live_keys = _extract_metric_keys(GOVERNANCE_KERNEL)
    if not frozen_keys or not live_keys:
        pytest.skip(
            f"Could not extract metric keys via AST "
            f"(frozen={len(frozen_keys)}, live={len(live_keys)}) "
            f"— REGISTRY may be built dynamically; skipping orphan check"
        )
    orphans = frozen_keys - live_keys
    assert not orphans, (
        f"Frozen kernel defines metric(s) absent from the live Governance kernel: {orphans}. "
        f"Either remove them from agentica_core/aggregate.py (frozen copy) or confirm they "
        f"belong in the live kernel. Orphan reducers mislead reviewers about which kernel "
        f"is the source of truth."
    )
