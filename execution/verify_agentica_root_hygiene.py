"""Verify the Agentica-Framework repo-root hygiene policy against the live repo root.

Third root-hygiene surface, alongside verify_root_hygiene (Order Samurai root)
and verify_claude_root_hygiene (~/.claude runtime root): validates
config/agentica_root_hygiene_policy.json against the Agentica-Framework repo root —
classification vocabulary, required entries, and unclassified top-level
entries. Unclassified entries WARN (drift pressure), missing required entries
FAIL.

Unlike the sibling verifiers, file entries in the policy may be glob patterns
(fnmatch), so the single active workstream handoff (HANDOFF-*.md) can live at
the repo root without a standing WARN.

Read-only and bounded by construction: the only disk access against the repo
root is a single top-level iterdir() plus per-entry existence checks.
"""

from __future__ import annotations

import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import agentica_repo_root
from execution.verify_claude_root_hygiene import (
    _load_json,
    _make_result,
    find_missing_required_entries,
    index_classified_entries,
    summarize,
)

# Same vocabulary as the Order Samurai repo-root policy (verify_root_hygiene),
# not the claude-home one — this surface is a source repo, not a runtime home.
AGENTICA_ROOT_CLASSIFICATIONS = {
    "archive",
    "dependency",
    "live",
    "metadata",
    "state",
    "support",
}

CLASSIFIED_SECTIONS = ("directories", "files")

#: None in a standalone distribution — see agentica_repo_root().
AGENTICA_REPO_ROOT = agentica_repo_root()
AGENTICA_ROOT_HYGIENE_POLICY_PATH = ROOT_DIR / "config" / "agentica_root_hygiene_policy.json"


def find_invalid_classifications(*, payload: dict) -> list[tuple[str, str]]:
    """Return (section, bucket) pairs whose bucket is outside the repo vocabulary."""
    invalid: list[tuple[str, str]] = []
    for section in CLASSIFIED_SECTIONS:
        for bucket in payload.get(section) or {}:
            if bucket not in AGENTICA_ROOT_CLASSIFICATIONS:
                invalid.append((section, bucket))
    return invalid


def find_unclassified_entries(*, root: Path, declared_entries: set[str]) -> list[str]:
    """Top-level entries not matched by any declared name or glob pattern."""
    unclassified: list[str] = []
    for entry in root.iterdir():
        name = entry.name
        if any(fnmatch(name, declared) for declared in declared_entries):
            continue
        unclassified.append(name)
    return sorted(unclassified, key=str.lower)


def run_checks(
    policy_path: Path = AGENTICA_ROOT_HYGIENE_POLICY_PATH,
    root: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    target_root = root if root is not None else AGENTICA_REPO_ROOT

    if target_root is None:
        # No Agentica repo above this tree — the public export ships the pack
        # standalone. Say so and check nothing, rather than measuring whichever
        # directory a fixed parent-hop happened to land on. Decided BEFORE the
        # policy is read: there is no repo to hold that policy accountable to.
        results.append(
            _make_result(
                "OK",
                "root_hygiene.agentica.not-applicable",
                "no Agentica repo root above this tree (standalone distribution); "
                "repo-layout hygiene is not measured here",
            )
        )
        return results

    payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(
            _make_result(
                "FAIL",
                "root_hygiene.agentica.policy",
                f"{policy_path.name}: {policy_error}",
            )
        )
        return results
    payload = payload or {}
    results.append(
        _make_result(
            "OK",
            "root_hygiene.agentica.policy",
            f"{policy_path.name} loaded",
        )
    )

    invalid_classifications = find_invalid_classifications(payload=payload)
    if invalid_classifications:
        for section, bucket in invalid_classifications:
            results.append(
                _make_result(
                    "FAIL",
                    f"root_hygiene.agentica.vocabulary.{bucket}",
                    f"{section} classification '{bucket}' is outside the repo vocabulary "
                    f"({', '.join(sorted(AGENTICA_ROOT_CLASSIFICATIONS))})",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.agentica.vocabulary",
                "all classification buckets are in the repo vocabulary",
            )
        )

    declared = index_classified_entries(payload=payload)

    if not target_root.is_dir():
        results.append(
            _make_result(
                "WARN",
                "root_hygiene.agentica.repo_root",
                f"repo root {target_root} does not exist on this machine; layout checks skipped",
            )
        )
        return results

    missing_required = find_missing_required_entries(payload=payload, root=target_root)
    if missing_required:
        for kind, entry, problem in missing_required:
            bucket = declared.get(entry, "undeclared")
            results.append(
                _make_result(
                    "FAIL",
                    f"root_hygiene.agentica.required.{bucket}",
                    f"required {kind} {problem} under {target_root}: {entry}",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.agentica.required",
                f"all required directories and files exist under {target_root}",
            )
        )

    unclassified = find_unclassified_entries(
        root=target_root,
        declared_entries=set(declared),
    )
    if unclassified:
        for name in unclassified:
            kind = "directory" if (target_root / name).is_dir() else "file"
            results.append(
                _make_result(
                    "WARN",
                    "root_hygiene.agentica.unclassified",
                    f"top-level {kind} not classified by the policy: {name}",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.agentica.unclassified",
                "all top-level repo-root entries are classified",
            )
        )

    return results


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['name']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
