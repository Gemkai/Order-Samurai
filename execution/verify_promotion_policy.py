from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import CONFIG_DIR, PROMOTION_POLICY_PATH, REPO_ROOT

ARCHITECTURE_SCORECARD_PATH = CONFIG_DIR / "architecture_scorecard.json"

# Gates the policy MUST declare for lifecycle governance to be real, not aspirational.
REQUIRED_LIFECYCLE_STATES = ("deprecated", "archive")
REQUIRED_PROMOTION_GATE_IDS = (
    "explicit-purpose",
    "canonical-pathing",
    "input-output-contract",
    "tests-or-smoke-check",
    "doctor-and-verifier-coverage",
    "doc-parity",
    "archive-boundary-clean",
)
REQUIRED_RETIREMENT_REQUIREMENTS = (
    "mark deprecated state explicitly",
    "remove live references before archival move",
    "retain historical material only behind archive boundaries",
)


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


def validate_lifecycle_states(*, payload: dict) -> list[str]:
    declared = set(payload.get("lifecycleStates") or [])
    return sorted(state for state in REQUIRED_LIFECYCLE_STATES if state not in declared)


def validate_promotion_checklist(*, payload: dict) -> list[str]:
    failures: list[str] = []
    checklist = payload.get("promotionChecklist")
    if not isinstance(checklist, list) or not checklist:
        return ["promotionChecklist absent or empty"]

    by_id = {item.get("id"): item for item in checklist if isinstance(item, dict)}

    for gate_id in REQUIRED_PROMOTION_GATE_IDS:
        item = by_id.get(gate_id)
        if item is None:
            failures.append(f"missing promotion gate '{gate_id}'")
            continue
        if item.get("required") is not True:
            failures.append(f"promotion gate '{gate_id}' is not marked required")
        if not (item.get("statement") or "").strip():
            failures.append(f"promotion gate '{gate_id}' has no statement")

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

    declared = set(requirements)
    return sorted(
        f"missing retirement requirement '{req}'"
        for req in REQUIRED_RETIREMENT_REQUIREMENTS
        if req not in declared
    )


def validate_scorecard_contract(*, payload: dict) -> list[str]:
    failures: list[str] = []
    lifecycle = next(
        (c for c in payload.get("categories", []) if c.get("id") == "lifecycle_governance"),
        None,
    )
    if lifecycle is None:
        return ["missing lifecycle_governance category"]

    required_artifacts = set(lifecycle.get("requiredArtifacts") or [])
    required_verifiers = set(lifecycle.get("requiredVerifiers") or [])

    if "config/promotion_policy.json" not in required_artifacts:
        failures.append("lifecycle_governance missing config/promotion_policy.json")
    if "execution/verify_promotion_policy.py" not in required_verifiers:
        failures.append("lifecycle_governance missing execution/verify_promotion_policy.py")

    return failures


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(PROMOTION_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "promotion_policy.json", policy_error))
        return results
    payload = policy_payload or {}

    state_failures = validate_lifecycle_states(payload=payload)
    if state_failures:
        results.append(
            _make_result(
                "FAIL",
                "promotion_policy.lifecycleStates",
                "missing lifecycle states: " + ", ".join(state_failures),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "promotion_policy.lifecycleStates",
                "lifecycle declares deprecated and archive states for retirement",
            )
        )

    checklist_failures = validate_promotion_checklist(payload=payload)
    if checklist_failures:
        results.append(
            _make_result(
                "FAIL",
                "promotion_policy.promotionChecklist",
                ", ".join(checklist_failures),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "promotion_policy.promotionChecklist",
                "all required promotion gates are present and marked required",
            )
        )

    blocker_failures = validate_blockers(payload=payload)
    if blocker_failures:
        results.append(
            _make_result("FAIL", "promotion_policy.blockers", ", ".join(blocker_failures))
        )
    else:
        results.append(
            _make_result(
                "OK",
                "promotion_policy.blockers",
                "promotion blockers are declared",
            )
        )

    retirement_failures = validate_retirement_policy(payload=payload)
    if retirement_failures:
        results.append(
            _make_result(
                "FAIL",
                "promotion_policy.retirementPolicy",
                ", ".join(retirement_failures),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "promotion_policy.retirementPolicy",
                "deprecation and retirement requirements are declared",
            )
        )

    scorecard_payload, scorecard_error = _load_json(ARCHITECTURE_SCORECARD_PATH)
    if scorecard_error:
        results.append(_make_result("FAIL", "architecture_scorecard.json", scorecard_error))
    else:
        scorecard_failures = validate_scorecard_contract(payload=scorecard_payload or {})
        if scorecard_failures:
            results.append(
                _make_result(
                    "FAIL",
                    "architecture_scorecard.json",
                    ", ".join(scorecard_failures),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "architecture_scorecard.json",
                    "lifecycle_governance category routes through promotion_policy.json and this verifier",
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
