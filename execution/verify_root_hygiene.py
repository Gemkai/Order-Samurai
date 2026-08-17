from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import (  # type: ignore[attr-defined]
    BASELINE_PROFILE,
    FULL_PROFILE,
    audit_profile,
    is_standalone_distribution,
    required_sections,
)
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


def pack_audit_profile(standalone: bool | None = None) -> str:
    """Which requirement tier applies to THIS pack. Unlike ~/.claude, inferable.

    The target here is the pack itself, so its layout answers the question the
    operator would otherwise have to answer by hand: a nested Agentica checkout is
    the development repo and must meet the full contract (backlog/, reports/,
    PROJECT.md, RONIN_SPEC.md — the charter and workflow this project runs on),
    while a standalone distribution is somebody else's tree, where those are this
    repo's conventions rather than universal invariants.

    Deriving it rather than reading an env var is deliberate. The strict tier for
    ~/.claude has existed since 2026-07-31 with a docstring saying to set
    ORDER_SAMURAI_AUDIT_PROFILE=full on a host that has the layout, and nothing
    outside tests has ever set it — so that tier has been silently off on the one
    machine documented as needing it. A default nobody remembers to override is a
    check that does not run. ORDER_SAMURAI_AUDIT_PROFILE still overrides both ways.

    `standalone` is injectable so both branches are testable from either tree --
    this suite ships inside the standalone pack, so a test that asserted "full"
    against the ambient layout would pass here and fail there.
    """
    if standalone is None:
        standalone = is_standalone_distribution()
    return audit_profile(default=BASELINE_PROFILE if standalone else FULL_PROFILE)


def resolve_required_sections(profile: str | None = None) -> tuple[str, str]:
    """(directories_key, files_key) for this pack's active tier."""
    return required_sections(profile or pack_audit_profile())


def index_declared_root_entries(*, payload: dict) -> set[str]:
    declared: set[str] = set()
    for section in ("directories", "files"):
        for entries in (payload.get(section) or {}).values():
            for entry in entries or []:
                normalized = _normalize_root_entry(entry)
                if normalized:
                    declared.add(normalized)
    return declared


def validate_root_hygiene_policy(
    *, payload: dict, repo_root: Path, sections: tuple[str, str] | None = None
) -> list[str]:
    """Validate the policy and the root it describes. `sections` pins the tier."""
    dirs_key, files_key = sections or resolve_required_sections()
    failures: list[str] = []
    declared_directories: set[str] = set()
    declared_files: set[str] = set()

    # A tier whose key the policy never declares must be loud. Reading an absent
    # key would yield an empty list and assert nothing -- the check would report
    # OK while enforcing no requirement at all, which is how a gate stops being a
    # gate. (verify_claude_root_hygiene.find_missing_required_entries carries the
    # same warning for the same reason.) An empty list is a real declaration and
    # is honoured; a missing key is a policy that does not support this tier.
    for key in (dirs_key, files_key):
        if key not in payload:
            failures.append(f"root_hygiene_policy: policy declares no {key}")

    for classification, entries in (payload.get("directories") or {}).items():
        if classification not in VALID_ROOT_CLASSIFICATIONS:
            failures.append(f"root_hygiene_policy: invalid classification {classification}")
        for entry in entries or []:
            normalized = _normalize_root_entry(entry)
            if normalized:
                declared_directories.add(normalized)

    for classification, entries in (payload.get("files") or {}).items():
        if classification not in VALID_ROOT_CLASSIFICATIONS:
            failures.append(f"root_hygiene_policy: invalid classification {classification}")
        for entry in entries or []:
            normalized = _normalize_root_entry(entry)
            if normalized:
                declared_files.add(normalized)

    for entry in payload.get(dirs_key, []):
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

    for entry in payload.get(files_key, []):
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
    # Parity with verify_claude_root_hygiene: the tier is printed so a run at the
    # lenient tier can never be mistaken for a run at the strict one.
    print(f"Profile: {pack_audit_profile()}  Root: {REPO_ROOT}")
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
