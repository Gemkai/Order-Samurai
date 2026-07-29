from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import ANTI_SPRAWL_POLICY_PATH, CONFIG_DIR, REPO_ROOT

# Anti-sprawl policy rules whose role is truth separation: a hand-maintained
# registry/manifest must resolve against on-disk reality. These rule ids declare
# the registry/manifest artifacts that answer existence questions.
TRUTH_SEPARATION_RULE_IDS = (
    "every-surface-must-be-classified",
    "discovery-must-be-factual",
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


def index_truth_separation_rules(*, payload: dict) -> dict[str, dict]:
    """Return the truth-separation rules actually declared in the policy."""
    by_id = {rule.get("id"): rule for rule in payload.get("rules", [])}
    return {
        rule_id: by_id[rule_id]
        for rule_id in TRUTH_SEPARATION_RULE_IDS
        if rule_id in by_id
    }


def _resolve_surface_root(*, payload: dict, repo_root: Path) -> Path:
    """A surface matrix may scope its entries to an external target root."""
    target = payload.get("targetRoot") or payload.get("targetRuntimeRoot")
    if target:
        return Path(str(target))
    return repo_root


def find_missing_registry_entries(*, registry_path: Path, repo_root: Path) -> list[str]:
    """List declared registry entries (surfaces[].path) that do not exist on disk."""
    payload, error = _load_json(registry_path)
    if error:
        return [f"registry unreadable: {error}"]

    base_root = _resolve_surface_root(payload=payload or {}, repo_root=repo_root)
    if not base_root.exists():
        # The whole registry points at a root that is gone: every entry is drift.
        return [f"target root missing: {base_root}"]

    missing: list[str] = []
    for surface in (payload or {}).get("surfaces", []):
        raw_path = str(surface.get("path") or "").strip()
        if not raw_path:
            continue
        if not (base_root / raw_path).exists():
            missing.append(raw_path)

    return sorted(set(missing))


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ANTI_SPRAWL_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "anti_sprawl_policy.json", policy_error))
        return results

    rules = index_truth_separation_rules(payload=policy_payload or {})
    if not rules:
        results.append(
            _make_result(
                "FAIL",
                "anti_sprawl_policy.json",
                "no truth-separation rules declared (expected "
                + ", ".join(TRUTH_SEPARATION_RULE_IDS)
                + ")",
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "anti_sprawl_policy.json",
            "truth-separation rules declared: " + ", ".join(sorted(rules)),
        )
    )

    # Step 1: every registry/manifest the policy expects must exist on disk.
    # A missing expected artifact is factual drift, not an advisory gap.
    declared_artifacts: list[tuple[str, str]] = []
    for rule_id, rule in sorted(rules.items()):
        severity = rule.get("severity", "high")
        for artifact in rule.get("expectedArtifacts", []):
            declared_artifacts.append((rule_id, artifact))

    if not declared_artifacts:
        results.append(
            _make_result(
                "FAIL",
                "truth-separation.artifacts",
                "truth-separation rules declare no expected registry/manifest artifacts",
            )
        )
        return results

    missing_artifacts = sorted(
        {
            artifact
            for _, artifact in declared_artifacts
            if not (repo_root / artifact).exists()
        }
    )
    if missing_artifacts:
        results.append(
            _make_result(
                "FAIL",
                "truth-separation.artifacts",
                "declared registry/manifest missing on disk: " + ", ".join(missing_artifacts),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "truth-separation.artifacts",
                "every declared registry/manifest exists on disk",
            )
        )

    # Step 2: for every registry/manifest that DOES exist, its declared entries
    # must resolve against on-disk reality. A registry listing entries that no
    # longer exist is factual drift -> FAIL.
    present_registries = sorted(
        {
            artifact
            for _, artifact in declared_artifacts
            if (repo_root / artifact).exists()
        }
    )
    for artifact in present_registries:
        missing_entries = find_missing_registry_entries(
            registry_path=repo_root / artifact,
            repo_root=repo_root,
        )
        if missing_entries:
            results.append(
                _make_result(
                    "FAIL",
                    f"registry-truth.{artifact}",
                    "declared entries not found on disk: " + ", ".join(missing_entries),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    f"registry-truth.{artifact}",
                    "all declared registry entries resolve on disk",
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
