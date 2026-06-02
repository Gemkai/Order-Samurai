from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import ANTI_DRIFT_POLICY_PATH, EXECUTION_DIR, REPO_ROOT

TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}

LIVE_SCAN_PATHS = (
    EXECUTION_DIR,
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


def scan_hardcoded_path_literals(
    *,
    scan_paths: Iterable[Path],
    path_literals: tuple[str, ...],
    base_root: Path = REPO_ROOT,
) -> list[str]:
    offenders: list[str] = []
    expanded_literals = set(path_literals)
    expanded_literals.update(literal.replace("\\", "\\\\") for literal in path_literals if "\\" in literal)

    for scan_path in scan_paths:
        if not scan_path.exists():
            continue

        files = (
            [scan_path]
            if scan_path.is_file()
            else [path for path in scan_path.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES]
        )

        for file_path in files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if any(literal in content for literal in expanded_literals):
                offenders.append(file_path.resolve().relative_to(base_root.resolve()).as_posix())

    return sorted(set(offenders))


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "anti_drift_policy.json", policy_error))
        return results

    rule_ids = {rule.get("id") for rule in (policy_payload or {}).get("rules", [])}
    if "single-path-authority" not in rule_ids:
        results.append(
            _make_result(
                "FAIL",
                "anti_drift_policy.json",
                "missing single-path-authority rule",
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "anti_drift_policy.json",
            "anti-drift policy loaded with single-path-authority rule",
        )
    )

    runtime_paths_path = EXECUTION_DIR / "runtime_paths.py"
    if not runtime_paths_path.exists():
        results.append(_make_result("FAIL", "runtime_paths.py", "missing canonical path authority"))
        return results
    results.append(_make_result("OK", "runtime_paths.py", "canonical path authority exists"))

    repo_root_literals = (
        str(repo_root),
        str(repo_root).replace("\\", "/"),
        str(repo_root).replace("\\", "\\\\"),
    )
    offenders = scan_hardcoded_path_literals(
        scan_paths=LIVE_SCAN_PATHS,
        path_literals=repo_root_literals,
        base_root=repo_root,
    )
    if offenders:
        results.append(_make_result("FAIL", "path-authority-scan", ", ".join(offenders)))
    else:
        results.append(
            _make_result(
                "OK",
                "path-authority-scan",
                "no hardcoded repo-local or machine-local absolute paths found in execution surface",
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
