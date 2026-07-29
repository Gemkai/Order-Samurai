"""Verify the Claude root-hygiene policy against the live Claude home layout.

Backlog item 8 (backlog/claude_verifier_backlog.md): validate
config/claude_root_hygiene_policy.json against the actual runtime_root()
top-level layout — classification vocabulary, required entries, and
unclassified top-level entries.

boundaryRules are intentionally NOT evaluated here: enforcing them
(forbidden patterns/roots inside live runtime surfaces) is backlog item 9's
job (execution/verify_claude_runtime_coupling.py).

Read-only and bounded by construction: the only disk access against the
runtime root is a single top-level iterdir() plus per-entry existence checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import ROOT_HYGIENE_POLICY_PATH, runtime_root

# The Claude-home classification vocabulary. Deliberately NOT the repo-root
# vocabulary — execution.verify_root_hygiene.validate_root_hygiene_policy
# hard-rejects these buckets, requires forbiddenRoots, and resolves existence
# under the REPO root, so it cannot be reused here.
CLAUDE_ROOT_CLASSIFICATIONS = {
    "runtime",
    "source",
    "generated_truth",
    "support",
    "state",
    "archive",
    "dependency",
    # `metadata` classifies root docs (AGENTS.md, CLAUDE.md, ...) — the live
    # claude_root_hygiene_policy.json uses a files.metadata bucket, same as the
    # repo-root policy. It belongs in the vocabulary, not flagged as drift.
    "metadata",
}

CLASSIFIED_SECTIONS = ("directories", "files")


def _normalize_entry(path_value: str) -> str:
    return str(path_value or "").strip().replace("\\", "/").strip("/")


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _make_result(status: str, name: str, detail: str) -> dict[str, str]:
    return {
        "status": status,
        "name": name,
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


def find_invalid_classifications(*, payload: dict) -> list[tuple[str, str]]:
    """Return (section, bucket) pairs whose bucket is outside the claude vocabulary."""
    invalid: list[tuple[str, str]] = []
    for section in CLASSIFIED_SECTIONS:
        for bucket in payload.get(section) or {}:
            if bucket not in CLAUDE_ROOT_CLASSIFICATIONS:
                invalid.append((section, bucket))
    return invalid


def index_classified_entries(*, payload: dict) -> dict[str, str]:
    """Map each declared top-level entry name to its classification bucket.

    Entries under an invalid bucket still count as declared — vocabulary drift
    is reported by find_invalid_classifications, not double-reported as
    unclassified entries.
    """
    declared: dict[str, str] = {}
    for section in CLASSIFIED_SECTIONS:
        for bucket, entries in (payload.get(section) or {}).items():
            for entry in entries or []:
                normalized = _normalize_entry(entry)
                if normalized:
                    declared.setdefault(normalized, bucket)
    return declared


def find_missing_required_entries(*, payload: dict, root: Path) -> list[tuple[str, str, str]]:
    """Return (kind, entry, problem) for each requiredDirectories/requiredFiles miss."""
    missing: list[tuple[str, str, str]] = []
    for kind, section, probe in (
        ("directory", "requiredDirectories", Path.is_dir),
        ("file", "requiredFiles", Path.is_file),
    ):
        for entry in payload.get(section) or []:
            normalized = _normalize_entry(entry)
            if not normalized:
                missing.append((kind, "<empty>", f"empty required {kind} path"))
                continue
            target = root / normalized
            if not target.exists():
                missing.append((kind, normalized, "missing"))
            elif not probe(target):
                missing.append((kind, normalized, f"not a {kind}"))
    return missing


def find_unclassified_entries(*, root: Path, declared_entries: set[str]) -> list[str]:
    """Mirror verify_root_hygiene.find_unclassified_root_entries, claude-aware.

    Dotfiles are exempt only while the policy itself declares no dotfile
    entries; once the policy classifies any dotfile, dotfiles are treated like
    any other entry.
    """
    ignore_dotfiles = not any(name.startswith(".") for name in declared_entries)
    unclassified: list[str] = []
    for entry in root.iterdir():
        name = entry.name
        if name in declared_entries:
            continue
        if ignore_dotfiles and name.startswith("."):
            continue
        unclassified.append(name)
    return sorted(unclassified, key=str.lower)


def run_checks(
    policy_path: Path = ROOT_HYGIENE_POLICY_PATH,
    root: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    target_root = root if root is not None else runtime_root()

    payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(
            _make_result(
                "FAIL",
                "root_hygiene.claude.policy",
                f"{policy_path.name}: {policy_error}",
            )
        )
        return results
    payload = payload or {}
    results.append(
        _make_result(
            "OK",
            "root_hygiene.claude.policy",
            f"{policy_path.name} loaded",
        )
    )

    invalid_classifications = find_invalid_classifications(payload=payload)
    if invalid_classifications:
        for section, bucket in invalid_classifications:
            results.append(
                _make_result(
                    "FAIL",
                    f"root_hygiene.claude.vocabulary.{bucket}",
                    f"{section} classification '{bucket}' is outside the claude vocabulary "
                    f"({', '.join(sorted(CLAUDE_ROOT_CLASSIFICATIONS))})",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.claude.vocabulary",
                "all classification buckets are in the claude vocabulary",
            )
        )

    declared = index_classified_entries(payload=payload)

    if not target_root.is_dir():
        # Missing runtime root on this machine: nothing to lay the policy
        # against — WARN and skip the layout checks rather than crash or emit
        # a FAIL cascade for every required entry.
        results.append(
            _make_result(
                "WARN",
                "root_hygiene.claude.runtime_root",
                f"runtime root {target_root} does not exist on this machine; layout checks skipped",
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
                    f"root_hygiene.claude.required.{bucket}",
                    f"required {kind} {problem} under {target_root}: {entry}",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.claude.required",
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
                    "root_hygiene.claude.unclassified",
                    f"top-level {kind} not classified by the policy: {name}",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "root_hygiene.claude.unclassified",
                "all top-level runtime-root entries are classified",
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
