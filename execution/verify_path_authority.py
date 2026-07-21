from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import ANTI_DRIFT_POLICY_PATH, EXECUTION_DIR, REPO_ROOT

# The scan covers the whole Governance code surface.
GOVERNANCE_ROOT = REPO_ROOT

TEXT_SUFFIXES = {
    ".py",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
}

# Curated code dirs (NOT the whole tree — keeps node_modules, .git, dashboard-ui, sub-bundles out).
LIVE_SCAN_PATHS = (
    GOVERNANCE_ROOT / "agentica_core",
    REPO_ROOT / "bin",
    REPO_ROOT / "execution",
    REPO_ROOT / "scouts",
    GOVERNANCE_ROOT / "api" / "src",
)

# Skipped while walking a scan dir: tests legitimately embed path-literal fixtures; the rest are
# build/cache/vendor noise. Applied only when run_checks passes it — the pure function defaults to
# no exclusions, so unit tests that scan an explicit sandbox under .tmp are unaffected.
_SCAN_EXCLUDE_PARTS = frozenset({
    "tests", "__pycache__", ".tmp", "node_modules", ".git",
    "dashboard-ui", "sub-bundles", ".ruff_cache", ".pytest_cache", "artifacts",
    # Auto-generated file register — lists absolute paths as data, not code env-leakage.
    ".quality_register.md",
})

# Machine-local absolute prefixes that are always wrong in committed code (env leakage). The old
# pre-consolidation Desktop location is the concrete signal; broad unix roots (/home, /Users) are
# handled separately by _HOME_PATH_RE below (substring matching them yields false positives).
MACHINE_LOCAL_LITERALS = (
    r"C:\Users\example\Desktop",
    "C:/Users/example/Desktop",
)

# Real per-user home directories are always machine-local leakage in committed code/config (they
# expose a username and break on any other machine). A regex — not a substring — so we match
# /Users/<name>/ and /home/<name>/ (and their C:\Users\<name>\ Windows form) generally, while still
# skipping the sanctioned `example` placeholder that tests and docs use. Requires a trailing
# separator so bare prose like "/home/..." (no real segment) does not false-positive.
_HOME_PATH_RE = re.compile(r"[/\\](?:Users|home)[/\\](?!example[/\\])[A-Za-z0-9._-]+[/\\]")

# Home-path leakage ships to users, so this scan is broader than the code scan: config/ is included
# because MCP/launcher configs are exactly where absolute local paths get pasted in.
HOME_PATH_SCAN_PATHS = LIVE_SCAN_PATHS + (REPO_ROOT / "config",)


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
    exclude_parts: frozenset = frozenset(),
) -> list[str]:
    offenders: list[str] = []
    expanded_literals = set(path_literals)
    expanded_literals.update(literal.replace("\\", "\\\\") for literal in path_literals if "\\" in literal)
    base = base_root.resolve()

    for scan_path in scan_paths:
        if not scan_path.exists():
            continue

        files = (
            [scan_path]
            if scan_path.is_file()
            else [path for path in scan_path.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES]
        )

        for file_path in files:
            if exclude_parts and (set(file_path.parts) & exclude_parts):
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if any(literal in content for literal in expanded_literals):
                resolved = file_path.resolve()
                try:
                    offenders.append(resolved.relative_to(base).as_posix())
                except ValueError:
                    offenders.append(resolved.as_posix())

    return sorted(set(offenders))


def scan_home_path_leaks(
    *,
    scan_paths: Iterable[Path],
    base_root: Path = REPO_ROOT,
    exclude_parts: frozenset = frozenset(),
) -> list[str]:
    """Flag files that embed a real per-user home directory (/Users/<name>/, /home/<name>/).

    Uses _HOME_PATH_RE so the `example` placeholder stays allowed while genuine usernames are
    caught anywhere in the scanned tree — including config/, which the code-literal scan skips.
    """
    offenders: list[str] = []
    base = base_root.resolve()

    for scan_path in scan_paths:
        if not scan_path.exists():
            continue

        files = (
            [scan_path]
            if scan_path.is_file()
            else [path for path in scan_path.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES]
        )

        for file_path in files:
            if exclude_parts and (set(file_path.parts) & exclude_parts):
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if _HOME_PATH_RE.search(content):
                resolved = file_path.resolve()
                try:
                    offenders.append(resolved.relative_to(base).as_posix())
                except ValueError:
                    offenders.append(resolved.as_posix())

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
    scan_paths = list(LIVE_SCAN_PATHS) + list(GOVERNANCE_ROOT.glob("*.py"))
    offenders = scan_hardcoded_path_literals(
        scan_paths=scan_paths,
        path_literals=repo_root_literals + MACHINE_LOCAL_LITERALS,
        base_root=GOVERNANCE_ROOT,
        exclude_parts=_SCAN_EXCLUDE_PARTS,
    )
    # These modules necessarily embed the literals they search for (MACHINE_LOCAL_LITERALS / the
    # drift-gate's stale literals), so they would always flag themselves — exclude both sources.
    _PATH_LITERAL_SOURCES = {"verify_path_authority.py", "verify_no_stale_paths.py"}
    offenders = [o for o in offenders if Path(o).name not in _PATH_LITERAL_SOURCES]
    if offenders:
        results.append(_make_result("FAIL", "path-authority-scan", ", ".join(offenders)))
    else:
        results.append(
            _make_result(
                "OK",
                "path-authority-scan",
                "no hardcoded repo-local or machine-local absolute paths found across the Governance code surface",
            )
        )

    home_offenders = scan_home_path_leaks(
        scan_paths=HOME_PATH_SCAN_PATHS,
        base_root=GOVERNANCE_ROOT,
        exclude_parts=_SCAN_EXCLUDE_PARTS,
    )
    # This module embeds the regex's own alternation ("Users"/"home") but never a real /Users/<name>/
    # literal, so it does not self-flag; exclude the literal sources anyway for symmetry.
    home_offenders = [o for o in home_offenders if Path(o).name not in _PATH_LITERAL_SOURCES]
    if home_offenders:
        results.append(_make_result("FAIL", "home-path-scan", ", ".join(home_offenders)))
    else:
        results.append(
            _make_result(
                "OK",
                "home-path-scan",
                "no real per-user home directories (/Users/<name>, /home/<name>) embedded in code or config",
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
