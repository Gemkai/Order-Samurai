from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import ANTI_SPRAWL_POLICY_PATH, CONFIG_DIR, REPO_ROOT

SURFACE_GOVERNANCE_RULE_ID = "every-surface-must-be-classified"
EXPECTED_VERIFIER = "execution/verify_surface_governance.py"
REQUIRED_SURFACE_FIELDS = ("role", "owner", "discoverabilityContract")


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {
        "status": status,
        "label": label,
        "detail": detail,
    }


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {
        "OK": 0,
        "WARN": 0,
        "FAIL": 0,
    }
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def find_surface_governance_rule(payload: dict) -> dict | None:
    for rule in payload.get("rules", []):
        if rule.get("id") == SURFACE_GOVERNANCE_RULE_ID:
            return rule
    return None


def resolve_expected_artifacts(rule: dict, repo_root: Path) -> tuple[list[str], list[str]]:
    """Split the rule's declared expectedArtifacts into (present, missing) repo-relative paths."""
    present: list[str] = []
    missing: list[str] = []
    for artifact in rule.get("expectedArtifacts", []):
        if (repo_root / artifact).is_file():
            present.append(artifact)
        else:
            missing.append(artifact)
    return present, missing


def validate_surface_entries(matrix_payload: dict) -> tuple[list[str], list[str]]:
    """Return (incomplete, unknown_role) findings.

    incomplete: surfaces missing one of role/owner/discoverabilityContract.
    unknown_role: surfaces whose role is not in the matrix's declared surfaceRoles.
    """
    incomplete: list[str] = []
    unknown_role: list[str] = []
    declared_roles = set(matrix_payload.get("surfaceRoles") or [])

    surfaces = matrix_payload.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return ["<no surfaces declared>"], []

    for index, surface in enumerate(surfaces):
        path = surface.get("path") or f"<surface #{index}>"
        missing_fields = [
            field
            for field in REQUIRED_SURFACE_FIELDS
            if not str(surface.get(field) or "").strip()
        ]
        if missing_fields:
            incomplete.append(f"{path} (missing {', '.join(missing_fields)})")

        role = str(surface.get("role") or "").strip()
        if declared_roles and role and role not in declared_roles:
            unknown_role.append(f"{path} (role '{role}' not in surfaceRoles)")

    return incomplete, unknown_role


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ANTI_SPRAWL_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "anti_sprawl_policy.json", policy_error))
        return results

    rule = find_surface_governance_rule(policy_payload or {})
    if rule is None:
        results.append(
            _make_result(
                "FAIL",
                "anti_sprawl_policy.json",
                f"missing {SURFACE_GOVERNANCE_RULE_ID} rule",
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "anti_sprawl_policy.json",
            f"surface governance rule '{SURFACE_GOVERNANCE_RULE_ID}' declared",
        )
    )

    if rule.get("verifier") != EXPECTED_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                "surface-governance.verifier-wiring",
                f"rule verifier is {rule.get('verifier')!r}, expected {EXPECTED_VERIFIER!r}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "surface-governance.verifier-wiring",
                f"rule routes through {EXPECTED_VERIFIER}",
            )
        )

    present_artifacts, missing_artifacts = resolve_expected_artifacts(rule, repo_root)
    if not rule.get("expectedArtifacts"):
        results.append(
            _make_result(
                "FAIL",
                "surface-governance.expected-artifacts",
                "rule declares no expectedArtifacts (cannot govern surfaces without a matrix)",
            )
        )
        return results
    if missing_artifacts:
        results.append(
            _make_result(
                "FAIL",
                "surface-governance.expected-artifacts",
                f"declared surface-matrix artifact(s) missing: {', '.join(missing_artifacts)}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "surface-governance.expected-artifacts",
                f"declared surface-matrix artifact(s) present: {', '.join(present_artifacts)}",
            )
        )

    # Validate every surface-matrix artifact that actually exists. Each surface must
    # carry a role, an owner, and a discoverability contract.
    matrix_paths = sorted(CONFIG_DIR.glob("*surface_matrix.json"))
    if not matrix_paths:
        results.append(
            _make_result(
                "FAIL",
                "surface-governance.matrices",
                "no surface-matrix artifact found under config/ to classify surfaces against",
            )
        )
        return results

    for matrix_path in matrix_paths:
        rel = matrix_path.resolve().relative_to(repo_root.resolve()).as_posix()
        matrix_payload, matrix_error = _load_json(matrix_path)
        if matrix_error:
            results.append(_make_result("FAIL", rel, matrix_error))
            continue

        incomplete, unknown_role = validate_surface_entries(matrix_payload or {})
        if incomplete:
            results.append(
                _make_result(
                    "FAIL",
                    rel,
                    "surfaces missing role/owner/discoverability: " + "; ".join(incomplete),
                )
            )
        if unknown_role:
            results.append(
                _make_result(
                    "WARN",
                    rel,
                    "surfaces with role outside declared surfaceRoles: " + "; ".join(unknown_role),
                )
            )
        if not incomplete and not unknown_role:
            surface_count = len(matrix_payload.get("surfaces") or [])
            results.append(
                _make_result(
                    "OK",
                    rel,
                    f"all {surface_count} surfaces carry a role, owner, and discoverability contract",
                )
            )

    return results


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
