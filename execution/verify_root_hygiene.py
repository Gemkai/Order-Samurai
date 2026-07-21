from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import REPO_ROOT, ROOT_HYGIENE_POLICY_PATH

VALID_ROOT_CLASSIFICATIONS = {
    "archive",
    "dependency",
    "live",
    "metadata",
    "state",
    "support",
}


def _normalize_root_entry(path_value: str) -> str:
    return str(path_value or "").strip().replace("\\", "/").strip("/")


def index_declared_root_entries(*, payload: dict) -> set[str]:
    declared: set[str] = set()
    for section in ("directories", "files"):
        for entries in payload.get(section, {}).values():
            for entry in entries:
                normalized = _normalize_root_entry(entry)
                if normalized:
                    declared.add(normalized)
    return declared


def validate_root_hygiene_policy(*, payload: dict, repo_root: Path) -> list[str]:
    failures: list[str] = []
    declared_directories: set[str] = set()
    declared_files: set[str] = set()

    for classification, entries in payload.get("directories", {}).items():
        if classification not in VALID_ROOT_CLASSIFICATIONS:
            failures.append(f"root_hygiene_policy: invalid classification {classification}")
        for entry in entries:
            normalized = _normalize_root_entry(entry)
            if normalized:
                declared_directories.add(normalized)

    for classification, entries in payload.get("files", {}).items():
        if classification not in VALID_ROOT_CLASSIFICATIONS:
            failures.append(f"root_hygiene_policy: invalid classification {classification}")
        for entry in entries:
            normalized = _normalize_root_entry(entry)
            if normalized:
                declared_files.add(normalized)

    for entry in payload.get("requiredDirectories", []):
        normalized = _normalize_root_entry(entry)
        if not normalized:
            failures.append("root_hygiene_policy: missing required directory path")
            continue
        target = repo_root / normalized
        if not target.exists() or not target.is_dir():
            failures.append(f"root_hygiene_policy: {normalized}")
            continue
        if normalized not in declared_directories:
            failures.append(f"root_hygiene_policy: required directory not declared {normalized}")

    for entry in payload.get("requiredFiles", []):
        normalized = _normalize_root_entry(entry)
        if not normalized:
            failures.append("root_hygiene_policy: missing required file path")
            continue
        target = repo_root / normalized
        if not target.exists() or not target.is_file():
            failures.append(f"root_hygiene_policy: {normalized}")
            continue
        if normalized not in declared_files:
            failures.append(f"root_hygiene_policy: required file not declared {normalized}")

    for rule in payload.get("boundaryRules", []):
        rule_name = rule.get("name", "unnamed")
        if not rule.get("scanPaths"):
            failures.append(f"root_hygiene_policy: boundary {rule_name} -> missing scan paths")
        if not rule.get("forbiddenRoots"):
            failures.append(f"root_hygiene_policy: boundary {rule_name} -> missing forbidden roots")

    return failures


def find_unclassified_root_entries(*, repo_root: Path, declared_entries: set[str]) -> list[str]:
    return sorted(
        [entry.name for entry in repo_root.iterdir() if entry.name not in declared_entries],
        key=str.lower,
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


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ROOT_HYGIENE_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "root_hygiene_policy.json", policy_error))
        return results

    failures = validate_root_hygiene_policy(payload=policy_payload or {}, repo_root=repo_root)
    if failures:
        results.append(_make_result("FAIL", "root_hygiene_policy.json", ", ".join(failures)))
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene_policy.json",
                "root hygiene policy validates declared top-level entries and boundary rules",
            )
        )

    declared_entries = index_declared_root_entries(payload=policy_payload or {})
    warnings = find_unclassified_root_entries(repo_root=repo_root, declared_entries=declared_entries)
    if warnings:
        results.append(_make_result("WARN", "root_hygiene.unclassified", ", ".join(warnings)))
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.unclassified",
                "all top-level root entries are classified",
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
