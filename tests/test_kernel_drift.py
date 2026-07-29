"""Drift tripwire between the two agentica_core kernels.

The repo-local kernel (this repo) is FROZEN; the Agentica OS Governance kernel
is the active one. Shared functions must stay semantically identical or
calibration/time-parsing silently diverges between the dashboard and the hub.

Two guards:
  1. Shared-function re-export identity — every SHARED_FUNCTIONS name must be
     aggregate.py's object re-exported by ronin_metrics, never a local copy
     (local copies are how the pre-2026-07-12 drift happened).
  2. Orphan-metric check — any metric name present in the frozen REGISTRY but
     absent from the live Governance kernel (its tuple REGISTRY plus the metric
     keys it injects via _set) is a vestigial duplicate that can mislead
     reviewers (caught Canary_Health, 2026-06-21).

Guard 2 silently no-op'd from the moment aggregate.py's REGISTRY became a list
of tuples — the extractor only understood dict rows, so live_keys came back
empty and the test skipped as "built dynamically" (found 2026-07-09). It now
parses both row shapes; the orphans that had accumulated while it slept are
frozen in KNOWN_ORPHANS_PENDING_DECISION awaiting a human verdict, and the
guard fails on any NEW orphan (or on a stale allowlist entry).

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

# Every function the frozen kernel re-exports from aggregate.py instead of
# duplicating (2026-07-12 dedup — the frozen copies had drifted from the
# corrected semantics, e.g. _kill_chains_disrupted lacked the _DISRUPT_ACTIONS
# filter and the absent-emitter data_gap flag).
SHARED_FUNCTIONS = [
    "_parse_iso",
    "_calibrate_coefficients",
    "_kill_chains_disrupted",
    "_estimated_agent_time_saved",
    "_estimated_cost_savings",
    "_estimated_human_time_saved",
    "_pending_chain_proposals",
]

# Both kernels use one of these field names for the metric identifier in REGISTRY dicts.
# Frozen kernel uses "metric"; Governance kernel may use "key".
_METRIC_FIELD_CANDIDATES = ("metric", "key")

# In the Governance kernel's tuple REGISTRY rows the metric key is the 3rd
# element: (pillar, group, key, reducer, live_tier, is_percent, is_count).
_TUPLE_KEY_INDEX = 2

# Orphans that had accumulated when the sleeping guard was re-armed (2026-07-09).
# All 15 were resolved 2026-07-12 (evidence and per-metric verdicts recorded in
# ronin_metrics.py's module docstring): 14 deleted from the frozen REGISTRY —
# concept already live under another name, dead source, or superseded mechanism —
# and MCP_vs_CLI_Ratio re-registered in the live kernel with corrected semantics.
# The set stays declared (empty) so the guard's stale-allowlist assert keeps
# enforcing that it can only shrink; any future orphan fails the test outright.
KNOWN_ORPHANS_PENDING_DECISION: frozenset[str] = frozenset()


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
    """Extract metric name strings from an ast.List of REGISTRY entries.

    Handles both row shapes: dict rows ({"metric"/"key": "Name", ...} — the
    frozen kernel) and tuple rows ((pillar, group, "Name", reducer, ...) — the
    Governance kernel). The dict-only version made the orphan guard silently
    skip for the entire tuple-registry era."""
    if not isinstance(node, ast.List):
        return set()
    result: set[str] = set()
    for elt in node.elts:
        if isinstance(elt, ast.Dict):
            for k, v in zip(elt.keys, elt.values):
                if (isinstance(k, ast.Constant) and k.value in _METRIC_FIELD_CANDIDATES
                        and isinstance(v, ast.Constant)):
                    result.add(str(v.value))
        elif (isinstance(elt, ast.Tuple) and len(elt.elts) > _TUPLE_KEY_INDEX
                and isinstance(elt.elts[_TUPLE_KEY_INDEX], ast.Constant)
                and isinstance(elt.elts[_TUPLE_KEY_INDEX].value, str)):
            result.add(elt.elts[_TUPLE_KEY_INDEX].value)
    return result


def _extract_injected_keys(source_path: Path) -> set[str]:
    """Metric keys the Governance kernel injects OUTSIDE its REGISTRY — the
    string literals passed as the key argument to ``_set(pillars, pillar,
    group, key, env)`` in build_pillars (verifier-derived, scout-signal and
    knowledge metrics). Without these, live metrics like Governance_Pass_Rate
    would read as orphans of the frozen registry."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_set" and len(node.args) >= 4
                and isinstance(node.args[3], ast.Constant)
                and isinstance(node.args[3].value, str)):
            result.add(node.args[3].value)
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
def test_shared_function_is_reexported_not_duplicated(func_name):
    """2026-07-12: the frozen kernel no longer carries its own copies of the
    shared/duplicated functions — it re-exports aggregate.py's (the corrected
    canonical implementations). Object identity is a strictly stronger no-drift
    guarantee than the old AST comparison, and unlike a body-level check it can
    never silently skip: a reintroduced local copy fails the identity assert."""
    if not GOVERNANCE_KERNEL.exists():
        pytest.skip(f"Governance kernel not found at {GOVERNANCE_KERNEL} "
                    f"(set AGENTICA_GOVERNANCE) — drift not checkable here")
    import sys
    if str(_GOVERNANCE_DIR) not in sys.path:
        sys.path.insert(0, str(_GOVERNANCE_DIR))
    import agentica_core.aggregate as agg
    import agentica_core.ronin_metrics as rm
    local_copy = _extract_function(LOCAL_KERNEL, func_name)
    assert local_copy is None, (
        f"{func_name} has been redefined locally in agentica_core/ronin_metrics.py. "
        f"The frozen kernel must re-export aggregate.py's implementation, not carry "
        f"its own copy — a local body silently drifts from the corrected semantics."
    )
    assert getattr(rm, func_name) is getattr(agg, func_name), (
        f"{func_name} in ronin_metrics is not the aggregate.py object. Restore the "
        f"re-export (from agentica_core.aggregate import {func_name})."
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
    live_keys = (_extract_metric_keys(GOVERNANCE_KERNEL)
                 | _extract_injected_keys(GOVERNANCE_KERNEL))
    if not frozen_keys or not live_keys:
        pytest.skip(
            f"Could not extract metric keys via AST "
            f"(frozen={len(frozen_keys)}, live={len(live_keys)}) "
            f"— REGISTRY may be built dynamically; skipping orphan check"
        )
    orphans = frozen_keys - live_keys
    new_orphans = orphans - KNOWN_ORPHANS_PENDING_DECISION
    assert not new_orphans, (
        f"Frozen kernel defines metric(s) absent from the live Governance kernel: {sorted(new_orphans)}. "
        f"Either remove them from agentica_core/ronin_metrics.py (frozen copy) or confirm they "
        f"belong in the live kernel. Orphan reducers mislead reviewers about which kernel "
        f"is the source of truth."
    )
    stale_allowlist = KNOWN_ORPHANS_PENDING_DECISION - orphans
    assert not stale_allowlist, (
        f"KNOWN_ORPHANS_PENDING_DECISION entries are no longer orphaned: {sorted(stale_allowlist)}. "
        f"Remove them from the allowlist so the guard stays accurate (the set may only shrink)."
    )
