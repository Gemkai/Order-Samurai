"""Cross-layer verifier — makes the Agentica boundary invariants (declared as prose in
`agentica_surface_matrix.json`) executable. Platform-INDEPENDENT: it checks the Agentica OS
layer structure itself, so it runs in the doctor regardless of which platform is resolved.

Invariant coverage (honest about what is statically checkable):
- layers-present / surfaces-resolve  -> the four layers and declared surfaces exist
- knowledge-purity (inv. "Knowledge holds what is true") -> no telemetry/logs misfiled in Knowledge
- execution-references (inv. "Execution does the work, holds references") -> Execution holds only refs
- governance-amendment (inv. "policy change needs human approval") -> NOT statically verifiable;
  we confirm the policy dir exists and state the gate is process-enforced.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from .types import VerifierResult

_THIS = Path(__file__).resolve()
LAYERS = ("Execution", "Knowledge", "Data", "Governance")

# Launcher script types invoked by scheduled tasks. Repo paths inside them must be
# derived at runtime (%~dp0 / $PSScriptRoot), never hardcoded absolute — a hardcoded
# path silently breaks (and keeps writing to a dead location) the moment the repo moves.
_LAUNCHER_SUFFIXES = (".cmd", ".bat", ".vbs", ".ps1")
# An absolute path literal that names the repo directory (with space, hyphen, or neither).
_ABS_REPO_PATH = re.compile(r"[A-Za-z]:[\\/][^\"'\r\n]*?Agentica[ \-]?OS[^\"'\r\n]*")


def _default_root() -> Path:
    # AGENTICA_OS_ROOT lets an external Agentica-OS install be targeted; otherwise resolve
    # relative to this repo (agentica_core -> Governance -> repo root).
    env = os.environ.get("AGENTICA_OS_ROOT")
    if env and Path(env).exists():
        return Path(env)
    return _THIS.parents[2]


def _matrix_path(root: Path) -> Path:
    if (root / "config").is_dir():
        return root / "config" / "agentica_surface_matrix.json"
    return root / "Governance" / "Order Samurai" / "config" / "agentica_surface_matrix.json"


def _make(status: str, label: str, detail: str) -> VerifierResult:
    return {"status": status, "label": label, "detail": detail}


def _is_reference(p: Path) -> bool:
    """True if p is a symlink OR a Windows junction (reparse point). Junctions report
    is_symlink()==False, so checking only symlinks would misclassify them."""
    if p.is_symlink():
        return True
    try:
        return bool(os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return False


def run_checks(root: Path | None = None) -> list[VerifierResult]:
    root = root or _default_root()
    results: list[VerifierResult] = []

    # 1. Layers present
    missing = [l for l in LAYERS if not (root / l).is_dir()]
    if missing:
        results.append(_make("FAIL", "layers-present", f"missing layer dirs: {', '.join(missing)}"))
    else:
        results.append(_make("OK", "layers-present", "Execution/Knowledge/Data/Governance all exist"))

    # 2 & 3. Surface matrix loads + declared surfaces resolve
    mpath = _matrix_path(root)
    if not mpath.exists():
        results.append(_make("FAIL", "surface-matrix", f"agentica_surface_matrix.json not found at {mpath}"))
    else:
        try:
            matrix = json.loads(mpath.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            matrix = None
            results.append(_make("FAIL", "surface-matrix", f"invalid surface matrix: {exc}"))
        if matrix is not None:
            surfaces = matrix.get("surfaces", [])
            results.append(_make("OK", "surface-matrix", f"loaded {len(surfaces)} declared surfaces"))
            unresolved = [s["path"] for s in surfaces if not (root / s["path"]).exists()]
            if unresolved:
                results.append(_make("FAIL", "surfaces-resolve", f"surfaces missing on disk: {', '.join(unresolved)}"))
            else:
                results.append(_make("OK", "surfaces-resolve", "all declared surfaces resolve on disk"))

    # 4. Knowledge purity — telemetry/logs belong in Data, not Knowledge
    knowledge = root / "Knowledge"
    if knowledge.is_dir():
        misfiled = []
        for pattern in ("*.jsonl", "*audit-report*.md"):
            for p in knowledge.rglob(pattern):
                rel = p.relative_to(knowledge)
                if any(part.startswith(".") for part in rel.parts):  # skip .obsidian/.git/etc.
                    continue
                misfiled.append(str(p.relative_to(root)))
        if misfiled:
            shown = ", ".join(misfiled[:8]) + (" ..." if len(misfiled) > 8 else "")
            results.append(_make("WARN", "knowledge-purity", f"operational artifacts misfiled in Knowledge (belong in Data): {shown}"))
        else:
            results.append(_make("OK", "knowledge-purity", "no telemetry/receipts/audit logs misfiled under Knowledge"))

    # 5. Execution holds references, not physical content
    execution = root / "Execution"
    if execution.is_dir():
        ALLOWED_PHYSICAL = {"README.md", "convert_agents.py", "Agents", "skills", ".DS_Store"}
        physical = [c.name for c in execution.iterdir() if c.name not in ALLOWED_PHYSICAL and not _is_reference(c)]
        if physical:
            results.append(_make("WARN", "execution-references", f"non-reference entries in Execution (expected symlinks/junctions): {', '.join(physical)}"))
        else:
            results.append(_make("OK", "execution-references", "Execution holds only references (symlinks/junctions) + README"))

    # 6. Governance amendment gate — process-enforced, not statically verifiable
    if (root / "config").is_dir():
        config = root / "config"
    else:
        config = root / "Governance" / "Order Samurai" / "config"
    if config.is_dir():
        results.append(_make("OK", "governance-amendment", "policy config present; changes are human-gated (approval is not statically verifiable)"))
    else:
        results.append(_make("FAIL", "governance-amendment", f"governance policy config not found at {config}"))

    return results


def main() -> int:
    from .verifiers import summarize

    results = run_checks()
    for r in results:
        print(f"[{r['status']}] {r['label']}: {r['detail']}")
    counts, exit_code = summarize(results)
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
