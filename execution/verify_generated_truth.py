from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import (
    ANTI_DRIFT_POLICY_PATH,
    CONFIG_DIR,
    REPO_ROOT,
)

ARCHITECTURE_SCORECARD_PATH = CONFIG_DIR / "architecture_scorecard.json"

# This verifier owns the "truth_separation" architecture category and the
# anti-drift rule "generated-truth-over-manual-inventory":
#   - Generated artifacts answer existence questions ("what exists").
#   - Hand-maintained artifacts answer policy / intent questions ("what is allowed / why").
# A hand-maintained inventory that shadows a generated source-of-truth is drift,
# and a declared generated artifact that is missing means existence questions have
# no authoritative answer. Either condition is a real FAIL.
TRUTH_RULE_ID = "generated-truth-over-manual-inventory"
TRUTH_CATEGORY_ID = "truth_separation"
SELF_VERIFIER = "execution/verify_generated_truth.py"

# Hand-maintained inventory candidates that, if present and treated as a
# source-of-truth for "what exists", would shadow a generated artifact.
# These are checked only against their corresponding generated producer.
SHADOW_INVENTORIES = (
    "docs/inventory.md",
    "docs/capability_inventory.md",
    "INVENTORY.md",
    "CAPABILITIES.md",
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


def find_truth_rule(*, payload: dict) -> dict | None:
    return next(
        (rule for rule in payload.get("rules", []) if rule.get("id") == TRUTH_RULE_ID),
        None,
    )


def find_truth_category(*, payload: dict) -> dict | None:
    return next(
        (cat for cat in payload.get("categories", []) if cat.get("id") == TRUTH_CATEGORY_ID),
        None,
    )


def missing_generated_artifacts(*, rule: dict, repo_root: Path) -> list[str]:
    """Declared generated artifacts (existence-answering producers) that are absent."""
    missing: list[str] = []
    for rel in rule.get("expectedArtifacts") or []:
        if not (repo_root / rel).is_file():
            missing.append(rel)
    return sorted(missing)


def shadowing_inventories(*, generated_present: bool, repo_root: Path) -> list[str]:
    """Hand-maintained inventories that shadow a generated source-of-truth.

    A hand-maintained .md inventory answering "what exists" is drift only when
    it stands in for a generated producer. If the generated producer is absent,
    the manual inventory is the de-facto (and forbidden) source-of-truth.
    """
    if generated_present:
        return []
    return sorted(
        rel for rel in SHADOW_INVENTORIES if (repo_root / rel).is_file()
    )


def stale_generated_artifacts(*, rule: dict, repo_root: Path) -> list[str]:
    """Generated artifacts older than a hand-maintained inventory that mirrors them.

    If a manual .md inventory is newer than the generated producer, the generated
    truth is stale relative to hand edits — an existence answer drifting behind
    intent edits. This is a WARN, not a hard FAIL.
    """
    inventory_mtimes = [
        (repo_root / rel).stat().st_mtime
        for rel in SHADOW_INVENTORIES
        if (repo_root / rel).is_file()
    ]
    if not inventory_mtimes:
        return []
    newest_inventory = max(inventory_mtimes)

    stale: list[str] = []
    for rel in rule.get("expectedArtifacts") or []:
        artifact = repo_root / rel
        if artifact.is_file() and artifact.stat().st_mtime < newest_inventory:
            stale.append(rel)
    return sorted(stale)


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    # 1. Policy must load. Missing policy => FAIL (never silently OK).
    policy_payload, policy_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "anti_drift_policy.json", policy_error))
        return results

    # 2. The truth-separation rule must be declared.
    rule = find_truth_rule(payload=policy_payload or {})
    if rule is None:
        results.append(
            _make_result(
                "FAIL",
                "anti_drift_policy.json",
                f"missing {TRUTH_RULE_ID} rule",
            )
        )
        return results

    # 3. The rule must route enforcement to this verifier.
    if rule.get("verifier") != SELF_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                "anti_drift_policy.json",
                f"{TRUTH_RULE_ID} verifier is {rule.get('verifier')!r}, expected {SELF_VERIFIER!r}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "anti_drift_policy.json",
                f"{TRUTH_RULE_ID} rule routes through {SELF_VERIFIER}",
            )
        )

    # 4. The scorecard truth_separation category must exist and name this verifier.
    scorecard_payload, scorecard_error = _load_json(ARCHITECTURE_SCORECARD_PATH)
    if scorecard_error:
        results.append(_make_result("FAIL", "architecture_scorecard.json", scorecard_error))
    else:
        category = find_truth_category(payload=scorecard_payload or {})
        if category is None:
            results.append(
                _make_result(
                    "FAIL",
                    "architecture_scorecard.json",
                    f"missing {TRUTH_CATEGORY_ID} category",
                )
            )
        elif SELF_VERIFIER not in set(category.get("requiredVerifiers") or []):
            results.append(
                _make_result(
                    "FAIL",
                    "architecture_scorecard.json",
                    f"{TRUTH_CATEGORY_ID} category does not require {SELF_VERIFIER}",
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "architecture_scorecard.json",
                    f"{TRUTH_CATEGORY_ID} category requires {SELF_VERIFIER}",
                )
            )

    # 5. Declared generated artifacts must exist — existence questions need an
    #    authoritative generated answer. Missing => FAIL.
    missing = missing_generated_artifacts(rule=rule, repo_root=repo_root)
    generated_present = not missing
    if missing:
        results.append(
            _make_result(
                "FAIL",
                "generated-truth.artifacts",
                "missing generated source-of-truth: " + ", ".join(missing),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "generated-truth.artifacts",
                "all declared generated source-of-truth artifacts exist",
            )
        )

    # 6. A hand-maintained inventory must not shadow a generated source-of-truth.
    shadows = shadowing_inventories(generated_present=generated_present, repo_root=repo_root)
    if shadows:
        results.append(
            _make_result(
                "FAIL",
                "generated-truth.shadow",
                "hand-maintained inventory shadows missing generated truth: " + ", ".join(shadows),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "generated-truth.shadow",
                "no hand-maintained inventory shadows a generated source-of-truth",
            )
        )

    # 7. Generated artifacts must not be stale relative to manual inventory edits.
    stale = stale_generated_artifacts(rule=rule, repo_root=repo_root)
    if stale:
        results.append(
            _make_result(
                "WARN",
                "generated-truth.staleness",
                "generated artifact older than hand-maintained inventory: " + ", ".join(stale),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "generated-truth.staleness",
                "generated artifacts are not stale relative to hand-maintained inventories",
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
