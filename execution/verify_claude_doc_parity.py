"""Verify Claude runtime contracts have their human-readable docs in place.

Backlog item 12 (backlog/claude_verifier_backlog.md): catch changes to runtime
contracts that land without the corresponding Claude human-readable docs.

This verifier reads the Documentation Parity category out of
config/claude_architecture_scorecard.json and, for each runtime surface it
declares, checks that the required human-readable doc exists under the live
Claude home (runtime_root(), CLAUDE_RUNTIME_ROOT-overridable):
CLAUDE.md, AGENTS.md, commands/doctor.md, directives/mcp-server-inventory.md.
It also checks that the enforcement-pack docs (the hardening report and the
verifier backlog) still exist in THIS repo.

"Docs must move in the same architectural batch as the contract" is a
git-history property — it cannot be verified statically at a single point in
time, so co-movement is emitted as one honor-system OK row (enforced by
review / commit-hygiene, not mechanically here). No git check is fabricated.

Read-only and bounded: the only disk access is per-doc existence checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import (
    BACKLOG_PATH,
    REPORT_PATH,
    is_standalone_distribution,
    SCORECARD_PATH,
    runtime_root,
)

# The scorecard category whose requiredRuntimeArtifacts are the human-readable
# docs that must track the runtime contracts.
DOC_PARITY_CATEGORY_ID = "documentation_parity"


def _normalize_doc(path_value: str) -> str:
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


def find_doc_parity_category(payload: dict) -> dict | None:
    for category in payload.get("categories", []):
        if category.get("id") == DOC_PARITY_CATEGORY_ID:
            return category
    return None


def required_runtime_docs(category: dict) -> list[str]:
    """The runtime-surface docs declared by the Documentation Parity category."""
    docs: list[str] = []
    for entry in category.get("requiredRuntimeArtifacts", []):
        normalized = _normalize_doc(entry)
        if normalized:
            docs.append(normalized)
    return docs


def run_checks(runtime_root_dir: Path | None = None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    payload, scorecard_error = _load_json(SCORECARD_PATH)
    if scorecard_error:
        results.append(
            _make_result(
                "FAIL",
                "doc_parity.scorecard",
                f"{SCORECARD_PATH.name}: {scorecard_error}",
            )
        )
        return results
    payload = payload or {}

    category = find_doc_parity_category(payload)
    if category is None:
        results.append(
            _make_result(
                "FAIL",
                "doc_parity.scorecard",
                f"{SCORECARD_PATH.name}: missing '{DOC_PARITY_CATEGORY_ID}' category",
            )
        )
        return results

    docs = required_runtime_docs(category)
    if not docs:
        results.append(
            _make_result(
                "FAIL",
                "doc_parity.scorecard",
                f"{SCORECARD_PATH.name}: '{DOC_PARITY_CATEGORY_ID}' declares no "
                "requiredRuntimeArtifacts to map runtime surfaces to docs",
            )
        )
        return results

    # Runtime-surface docs must exist under the live Claude home. A missing
    # runtime root on this machine means we cannot see those docs at all: WARN
    # each rather than crash or emit a misleading FAIL cascade. The in-repo
    # enforcement-pack checks below still run regardless.
    target_root = runtime_root_dir if runtime_root_dir is not None else runtime_root()
    root_exists = target_root.is_dir()

    for doc in docs:
        label = f"doc_parity.runtime.{doc}"
        if not root_exists:
            results.append(
                _make_result(
                    "WARN",
                    label,
                    f"runtime root {target_root} does not exist on this machine; "
                    f"cannot verify runtime doc {doc}",
                )
            )
            continue
        target = target_root / doc
        if target.is_file():
            results.append(
                _make_result(
                    "OK",
                    label,
                    f"runtime doc present under {target_root}: {doc}",
                )
            )
        else:
            results.append(
                _make_result(
                    "FAIL",
                    label,
                    f"required runtime doc missing under {target_root}: {doc}",
                )
            )

    # Enforcement-pack docs live in THIS repo and must not vanish — except in a
    # standalone distribution, where the exporter deliberately never ships the
    # internal hardening report. Absent-by-design is not the same finding as
    # absent-by-rot, so it does not get the same status.
    standalone = is_standalone_distribution()
    for path in (REPORT_PATH, BACKLOG_PATH):
        rel = path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
        label = f"doc_parity.repo.{rel}"
        if path.is_file():
            results.append(_make_result("OK", label, f"enforcement-pack doc present: {rel}"))
        elif standalone:
            results.append(
                _make_result("OK", label, f"not shipped in a standalone distribution: {rel}")
            )
        else:
            results.append(
                _make_result("FAIL", label, f"enforcement-pack doc missing: {rel}")
            )

    # Co-movement ("docs land in the same batch as the contract they document")
    # is a property of git history, not of the tree at any single instant. It is
    # deliberately NOT faked with a git check here.
    results.append(
        _make_result(
            "OK",
            "doc_parity.co-movement",
            "docs-move-with-contracts is a git-history property; enforced by review "
            "and commit hygiene, not statically checkable at a point in time",
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
