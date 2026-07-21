from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import PROMOTION_POLICY_PATH, REPO_ROOT, ROOT_HYGIENE_POLICY_PATH

TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}


def _normalize_root_entry(path_value: str) -> str:
    return str(path_value or "").strip().replace("\\", "/").strip("/")


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


def scan_archive_boundary_violations(
    *,
    scan_paths: Iterable[Path],
    forbidden_roots: tuple[str, ...],
    base_root: Path = REPO_ROOT,
) -> list[str]:
    offenders: list[str] = []
    patterns = {
        root: [
            re.compile(rf'["\']{re.escape(root)}(?:[/\\\\])', re.IGNORECASE),
            re.compile(rf'["\'][^"\']*(?:[/\\\\]){re.escape(root)}(?:[/\\\\])', re.IGNORECASE),
            re.compile(rf'\bPath\(\s*["\']{re.escape(root)}["\']\s*\)', re.IGNORECASE),
            re.compile(
                rf'\b(?:REPO_ROOT|repo_root|PROJECT_ROOT|project_root)\s*/\s*["\']{re.escape(root)}["\']',
                re.IGNORECASE,
            ),
        ]
        for root in forbidden_roots
    }

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
            relative_path = file_path.resolve().relative_to(base_root.resolve()).as_posix()
            for root, matchers in patterns.items():
                if any(matcher.search(content) for matcher in matchers):
                    offenders.append(f"{relative_path} -> {root}")

    return sorted(set(offenders))


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    root_policy_payload, root_policy_error = _load_json(ROOT_HYGIENE_POLICY_PATH)
    if root_policy_error:
        results.append(_make_result("FAIL", "root_hygiene_policy.json", root_policy_error))
        return results
    results.append(_make_result("OK", "root_hygiene_policy.json", "root hygiene policy loaded"))

    promotion_payload, promotion_error = _load_json(PROMOTION_POLICY_PATH)
    if promotion_error:
        results.append(_make_result("FAIL", "promotion_policy.json", promotion_error))
        return results
    if not promotion_payload.get("promotionChecklist"):
        results.append(_make_result("FAIL", "promotion_policy.json", "missing promotion checklist"))
        return results
    results.append(_make_result("OK", "promotion_policy.json", "promotion policy loaded"))

    boundary_offenders: list[str] = []
    for rule in root_policy_payload.get("boundaryRules", []):
        scan_paths = [
            repo_root / _normalize_root_entry(scan_path)
            for scan_path in rule.get("scanPaths", [])
            if _normalize_root_entry(scan_path)
        ]
        forbidden_roots = tuple(
            _normalize_root_entry(root)
            for root in rule.get("forbiddenRoots", [])
            if _normalize_root_entry(root)
        )
        boundary_offenders.extend(
            scan_archive_boundary_violations(
                scan_paths=scan_paths,
                forbidden_roots=forbidden_roots,
                base_root=repo_root,
            )
        )

    if boundary_offenders:
        results.append(
            _make_result(
                "FAIL",
                "archive-boundary-scan",
                ", ".join(sorted(set(boundary_offenders))),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "archive-boundary-scan",
                "live runtime surfaces do not reference archive or exploratory roots",
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
