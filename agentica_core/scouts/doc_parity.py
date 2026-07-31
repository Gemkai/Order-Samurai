"""ARTS-001: Documentation parity scout.

For each .py file changed in the Order Samurai repo in the last 7 days,
checks whether a matching docs/solutions/*.md exists (by matching the
stem/module name in the file's YAML frontmatter ``module:`` field).

Returns::

    {
        "doc_parity_issues": <int>,   # count of .py files with no matching doc
        "stale_files": [...]           # list of undocumented .py file paths
    }
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Order Samurai repo root: env override, else derived relative to this file (no dead absolute path).
# agentica_core/scouts/doc_parity.py -> parents[2] == Governance, which holds "Order Samurai".
REPO = os.environ.get("ORDER_SAMURAI_ROOT") or str(Path(__file__).resolve().parents[2] / "Order Samurai")
DOCS_ROOT = Path(REPO) / "docs" / "solutions"


def _changed_py_files() -> list[str]:
    """Return .py file paths changed in the last 7 days (added or modified)."""
    result = subprocess.run(
        [
            "git",
            "-C", REPO,
            "log",
            "--since=7 days ago",
            "--name-only",
            "--diff-filter=AM",
            "--format=",
            # Scope to the Order Samurai subtree. REPO is a subdirectory of the
            # enclosing monorepo, so `git -C REPO` resolves to that repo's
            # toplevel and would otherwise count every .py changed repo-wide
            # (636 vs the 8 actually under Order Samurai). The `.` pathspec is
            # relative to REPO and limits the walk to this subtree.
            "--",
            ".",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    files: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.endswith(".py"):
            files.append(line)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def _has_doc(py_path: str) -> bool:
    """Return True if any docs/solutions/**/*.md references this module.

    Matches when the markdown file's ``module:`` frontmatter value contains
    the stem of the .py file (case-insensitive).
    """
    stem = Path(py_path).stem.lower().replace("_", " ")
    stem_underscore = Path(py_path).stem.lower()
    if not DOCS_ROOT.exists():
        return False
    for md in DOCS_ROOT.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Extract module: line from frontmatter (between --- delimiters)
        in_front = False
        for line in text.splitlines():
            if line.strip() == "---":
                if not in_front:
                    in_front = True
                    continue
                else:
                    break  # end of frontmatter
            if in_front and line.strip().startswith("module:"):
                value = line.split(":", 1)[1].strip().lower().strip('"').strip("'")
                if stem in value or stem_underscore in value:
                    return True
    return False


# Solution docs describe changes to *runtime solution code*. Per
# config/hub_surface_matrix.json, execution/ and scouts/ are the `runtime`
# surfaces; tests/ is `quality_gate`, Research/ is `knowledge_source`, bin/ is
# `operator`, and docs/artifacts/state/reports/backlog/prompts/soji have their
# own contracts. Doc-parity governs runtime code only, so a change to a test or
# a research note does not, by itself, demand a solution doc.
_SOLUTION_CODE_SURFACES = ("execution/", "scouts/")


def _is_solution_code(py_path: str) -> bool:
    """True if the changed .py is runtime solution code (execution/ or scouts/),
    matched on the Order-Samurai-relative segment of the path."""
    norm = py_path.replace("\\", "/")
    tail = norm.split("Order Samurai/", 1)[-1]
    return tail.startswith(_SOLUTION_CODE_SURFACES)


def run() -> dict:
    """Scout entry point. Returns doc_parity_issues count and stale_files list."""
    changed = [f for f in _changed_py_files() if _is_solution_code(f)]
    stale: list[str] = [f for f in changed if not _has_doc(f)]
    return {
        "doc_parity_issues": len(stale),
        "stale_files": stale,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run()))
