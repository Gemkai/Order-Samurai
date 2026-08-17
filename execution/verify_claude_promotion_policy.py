"""Backlog item 10: verify the Claude runtime promotion policy contract.

Ensures Claude runtime assets satisfy the promotion checklist before entering
live runtime, operator, or compatibility planes. Consumes
config/claude_promotion_policy.json + config/claude_surface_matrix.json and
mirrors execution/verify_promotion_policy.py retargeted at the claude pack:

- the policy loads with lifecycleStates/promotionChecklist/blockers/
  retirementPolicy intact, and every checklist item has id/required/statement
- the backlog-mandated gates (owner+purpose, generated-config integration,
  doctor/invariant visibility) are present and marked required
- every surface-matrix entry's role maps to a declared lifecycle-compatible
  role in the policy's lifecycleStates
- mechanically checkable checklist requirements are enforced against the
  matrix: owner non-empty everywhere, discoverabilityContract non-empty for
  live-runtime-plane surfaces
- judgment-only checklist items are counted in a single honor-system OK row,
  never fabricated into fake checks
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import PROMOTION_POLICY_PATH, SURFACE_MATRIX_PATH

# Retirement cannot be governed without these lifecycle states declared.
REQUIRED_LIFECYCLE_STATES = ("deprecated", "archive")

# Backlog item 10 acceptance criteria: owner and purpose are required,
# generated-config integration is required, doctor/invariant visibility is
# required. These ids must exist in the checklist and be marked required.
REQUIRED_PROMOTION_GATE_IDS = (
    "explicit-purpose-owner",
    "generated-config-integration",
    "doctor-and-invariant-coverage",
)

# Checklist gates that this verifier enforces mechanically against the surface
# matrix. Everything else in the checklist is judgment-only (honor-system).
MECHANICAL_GATE_IDS = {
    "explicit-purpose-owner": "owner non-empty on every surface-matrix entry",
    "inventory-parity": "discoverabilityContract non-empty on live-runtime-plane surfaces",
}

# Surface-matrix role -> promotion-policy lifecycle state. A surface whose role
# is absent here (or whose mapped state the policy does not declare) cannot be
# lifecycle-governed and fails the check.
ROLE_LIFECYCLE_MAP = {
    "runtime": "runtime",
    "registry": "runtime",
    "generator": "runtime",
    "generated_truth": "runtime",
    "operator": "runtime",
    "state": "runtime",
    "support": "runtime",
    "dependency": "runtime",
    "compatibility": "compatibility",
    "source": "source",
    "archive": "archive",
}

# Roles that live on the runtime plane and therefore owe operators a
# discoverability contract (inventory-parity, mechanically checked).
RUNTIME_PLANE_ROLES = frozenset(
    role for role, state in ROLE_LIFECYCLE_MAP.items() if state == "runtime"
)


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def validate_lifecycle_states(*, payload: dict) -> list[str]:
    declared = payload.get("lifecycleStates")
    if not isinstance(declared, list) or not declared:
        return ["lifecycleStates absent or empty"]
    declared_set = set(declared)
    return sorted(
        f"missing lifecycle state '{state}'"
        for state in REQUIRED_LIFECYCLE_STATES
        if state not in declared_set
    )


def validate_promotion_checklist(*, payload: dict) -> list[str]:
    failures: list[str] = []
    checklist = payload.get("promotionChecklist")
    if not isinstance(checklist, list) or not checklist:
        return ["promotionChecklist absent or empty"]

    by_id: dict[str, dict] = {}
    for index, item in enumerate(checklist):
        if not isinstance(item, dict):
            failures.append(f"checklist item #{index} is not an object")
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            failures.append(f"checklist item #{index} has no id")
            continue
        by_id[item_id] = item
        if not isinstance(item.get("required"), bool):
            failures.append(f"checklist item '{item_id}' has no boolean 'required' flag")
        if not str(item.get("statement") or "").strip():
            failures.append(f"checklist item '{item_id}' has no statement")

    for gate_id in REQUIRED_PROMOTION_GATE_IDS:
        item = by_id.get(gate_id)
        if item is None:
            failures.append(f"missing promotion gate '{gate_id}'")
        elif item.get("required") is not True:
            failures.append(f"promotion gate '{gate_id}' is not marked required")

    return failures


def validate_blockers(*, payload: dict) -> list[str]:
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        return ["blockers list absent or empty"]
    return []


def validate_retirement_policy(*, payload: dict) -> list[str]:
    retirement = payload.get("retirementPolicy")
    if not isinstance(retirement, dict):
        return ["retirementPolicy absent"]
    requirements = retirement.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return ["retirementPolicy.requirements absent or empty"]
    return []


def validate_role_lifecycle_mapping(*, matrix_payload: dict, policy_payload: dict) -> list[str]:
    """Every surface entry's role must map to a lifecycle state the policy declares."""
    failures: list[str] = []
    surfaces = matrix_payload.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return ["surface matrix declares no surfaces"]

    declared_states = set(policy_payload.get("lifecycleStates") or [])
    for index, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            failures.append(f"surface #{index} is not an object")
            continue
        path = str(surface.get("path") or "").strip() or f"<surface #{index}>"
        role = str(surface.get("role") or "").strip()
        if not role:
            failures.append(f"{path}: no role declared")
            continue
        lifecycle = ROLE_LIFECYCLE_MAP.get(role)
        if lifecycle is None:
            failures.append(f"{path}: role '{role}' has no lifecycle mapping")
            continue
        if lifecycle not in declared_states:
            failures.append(
                f"{path}: role '{role}' maps to lifecycle state '{lifecycle}' "
                "which the promotion policy does not declare"
            )
    return failures


def validate_matrix_promotion_requirements(*, matrix_payload: dict) -> list[str]:
    """Mechanically checkable checklist gates enforced against the matrix:
    owner non-empty everywhere; discoverabilityContract non-empty on
    live-runtime-plane surfaces."""
    failures: list[str] = []
    surfaces = matrix_payload.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return ["surface matrix declares no surfaces"]

    for index, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            continue  # already reported by the lifecycle-mapping check
        path = str(surface.get("path") or "").strip() or f"<surface #{index}>"
        if not str(surface.get("owner") or "").strip():
            failures.append(f"{path}: owner is empty (explicit-purpose-owner gate)")
        role = str(surface.get("role") or "").strip()
        if role in RUNTIME_PLANE_ROLES and not str(
            surface.get("discoverabilityContract") or ""
        ).strip():
            failures.append(
                f"{path}: runtime-plane surface has no discoverabilityContract "
                "(inventory-parity gate)"
            )
    return failures


def honor_system_gate_ids(*, payload: dict) -> list[str]:
    """Checklist gates that are judgment-only: present in the policy but not
    mechanically checkable against the surface matrix."""
    checklist = payload.get("promotionChecklist")
    if not isinstance(checklist, list):
        return []
    ids = []
    for item in checklist:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id not in MECHANICAL_GATE_IDS:
            ids.append(item_id)
    return ids


def run_checks(
    policy_path: Path = PROMOTION_POLICY_PATH,
    matrix_path: Path = SURFACE_MATRIX_PATH,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(_make_result("FAIL", "claude_promotion_policy.json", policy_error))
        return results
    payload = policy_payload or {}

    results.extend(_check_policy_sections(payload))
    _check_surface_matrix(results, matrix_path, payload)
    _check_honor_system_gates(results, payload)
    return results


#: The four policy sections that share one shape: a validator returning failure
#: strings, a FAIL row joining them, and a fixed OK sentence. Written once as data
#: rather than four times as near-identical branches — the sentences are the only
#: thing that actually differed.
_POLICY_SECTIONS = (
    ("lifecycleStates", validate_lifecycle_states,
     "lifecycle declares deprecated and archive states for retirement"),
    ("promotionChecklist", validate_promotion_checklist,
     "every checklist item carries id/required/statement and the "
     "owner, generated-config, and doctor-visibility gates are required"),
    ("blockers", validate_blockers,
     "promotion blockers are declared"),
    ("retirementPolicy", validate_retirement_policy,
     "deprecation and retirement requirements are declared"),
)


def _check_policy_sections(payload: dict) -> list[dict[str, str]]:
    """One row per declared policy section, in declaration order."""
    rows: list[dict[str, str]] = []
    for section, validate, ok_detail in _POLICY_SECTIONS:
        label = f"claude_promotion_policy.{section}"
        failures = validate(payload=payload)
        if failures:
            rows.append(_make_result("FAIL", label, ", ".join(failures)))
        else:
            rows.append(_make_result("OK", label, ok_detail))
    return rows


def _check_surface_matrix(
    results: list[dict[str, str]], matrix_path: Path, payload: dict
) -> None:
    """The surface matrix agrees with the promotion policy."""
    matrix_payload, matrix_error = _load_json(matrix_path)
    if matrix_error:
        results.append(_make_result("FAIL", "claude_surface_matrix.json", matrix_error))
    else:
        matrix = matrix_payload or {}

        mapping_failures = validate_role_lifecycle_mapping(
            matrix_payload=matrix, policy_payload=payload
        )
        if mapping_failures:
            results.append(
                _make_result(
                    "FAIL",
                    "claude_surface_matrix.role_lifecycle",
                    "; ".join(mapping_failures),
                )
            )
        else:
            surface_count = len(matrix.get("surfaces") or [])
            results.append(
                _make_result(
                    "OK",
                    "claude_surface_matrix.role_lifecycle",
                    f"all {surface_count} surface roles map to declared lifecycle states",
                )
            )

        requirement_failures = validate_matrix_promotion_requirements(matrix_payload=matrix)
        if requirement_failures:
            results.append(
                _make_result(
                    "FAIL",
                    "claude_surface_matrix.promotion_requirements",
                    "; ".join(requirement_failures),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "claude_surface_matrix.promotion_requirements",
                    "every surface names an owner and runtime-plane surfaces "
                    "declare a discoverability contract",
                )
            )



def _check_honor_system_gates(results: list[dict[str, str]], payload: dict) -> None:
    """Gates the policy marks honor-system are reported as such."""
    honor_ids = honor_system_gate_ids(payload=payload)
    if honor_ids:
        checklist = payload.get("promotionChecklist") or []
        results.append(
            _make_result(
                "OK",
                "claude_promotion_policy.honor_system",
                f"{len(honor_ids)} of {len(checklist)} checklist gates are "
                "judgment-only (honor-system, not mechanically checkable): "
                + ", ".join(honor_ids),
            )
        )


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
