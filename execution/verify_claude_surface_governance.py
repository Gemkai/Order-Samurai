"""Verify every major Claude surface has a declared role, owner, and
discoverability contract, and that declared surfaces line up with the live
runtime root.

Backlog item 7 (claude_verifier_backlog.md). Consumes
config/claude_surface_matrix.json:

  (a) structural validation is delegated to
      execution.verify_surface_governance.validate_surface_entries;
  (b) declared surfaces must exist under runtime_root() — a missing surface
      is FAIL for role=runtime, WARN for every other role;
  (c) every surface role must be a member of the matrix's own surfaceRoles;
  (d) compatibility-role surfaces must name a canonical owner distinct from
      the surface itself instead of competing with it.

Read-only: only existence of the matrix-declared paths is probed; no
directory trees are walked and no runtime file contents are read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import (
    BASELINE_PROFILE,
    SURFACE_MATRIX_PATH,
    audit_profile,
    runtime_root,
)
from execution.verify_surface_governance import validate_surface_entries

RUNTIME_ROLE = "runtime"
COMPAT_ROLE_MARKER = "compat"


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _normalize_surface_path(path_value: str) -> str:
    return str(path_value or "").strip().replace("\\", "/").strip("/")


def _surfaces(payload: dict) -> list[dict]:
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, list):
        return []
    return [surface for surface in surfaces if isinstance(surface, dict)]


def check_surface_existence(*, payload: dict, root: Path) -> tuple[list[str], list[str]]:
    """Split matrix surfaces missing under the runtime root into
    (missing_runtime, missing_other) lists of declared paths."""
    missing_runtime: list[str] = []
    missing_other: list[str] = []
    for surface in _surfaces(payload):
        normalized = _normalize_surface_path(surface.get("path"))
        if not normalized:
            continue  # structural validation already reports pathless surfaces
        if (root / normalized).exists():
            continue
        role = str(surface.get("role") or "").strip()
        if role == RUNTIME_ROLE:
            missing_runtime.append(f"{normalized} (role {role})")
        else:
            missing_other.append(f"{normalized} (role {role or 'unset'})")
    return missing_runtime, missing_other


def check_compat_ownership(*, payload: dict) -> list[str]:
    """Compatibility-role surfaces must declare a canonical owner that is not
    the surface itself; a compat shim that owns itself is competing with the
    canonical surface instead of pointing at it."""
    findings: list[str] = []
    for surface in _surfaces(payload):
        role = str(surface.get("role") or "").strip().lower()
        if COMPAT_ROLE_MARKER not in role:
            continue
        normalized = _normalize_surface_path(surface.get("path"))
        label = normalized or "<pathless surface>"
        owner = str(surface.get("owner") or "").strip()
        if not owner:
            findings.append(f"{label} (compat surface declares no owner)")
        elif _normalize_surface_path(owner) == normalized:
            findings.append(f"{label} (compat surface names itself as owner)")
    return findings


def run_checks(
    *,
    matrix_path: Path = SURFACE_MATRIX_PATH,
    root: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    matrix_payload, matrix_error = _load_json(matrix_path)
    if matrix_error:
        results.append(_make_result("FAIL", matrix_path.name, matrix_error))
        return results
    payload = matrix_payload or {}

    # (a) structural validation — reuse the shared surface-entry validator.
    incomplete, unknown_role = validate_surface_entries(payload)
    if incomplete:
        results.append(
            _make_result(
                "FAIL",
                "claude-surface-governance.structure",
                "surfaces missing role/owner/discoverability: " + "; ".join(incomplete),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-surface-governance.structure",
                f"all {len(_surfaces(payload))} surfaces carry a role, owner, and discoverability contract",
            )
        )

    # (c) every role must belong to the matrix's own surfaceRoles vocabulary.
    if not payload.get("surfaceRoles"):
        results.append(
            _make_result(
                "FAIL",
                "claude-surface-governance.roles",
                "matrix declares no surfaceRoles vocabulary to validate roles against",
            )
        )
    elif unknown_role:
        results.append(
            _make_result(
                "FAIL",
                "claude-surface-governance.roles",
                "surfaces with role outside declared surfaceRoles: " + "; ".join(unknown_role),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-surface-governance.roles",
                "every surface role is a member of the matrix's declared surfaceRoles",
            )
        )

    # (b) declared surfaces must exist under the live runtime root.
    live_root = root if root is not None else runtime_root()
    if not live_root.is_dir():
        results.append(
            _make_result(
                "WARN",
                "claude-surface-governance.existence",
                f"runtime root {live_root} not present on this machine; existence checks skipped",
            )
        )
    else:
        missing_runtime, missing_other = check_surface_existence(payload=payload, root=live_root)
        if missing_runtime:
            # The declared runtime surfaces are this control plane's own scripts.
            # On the baseline profile the target is any Claude Code install, which
            # has none of them by design — see claude_runtime_target.audit_profile.
            baseline = audit_profile() == BASELINE_PROFILE
            results.append(
                _make_result(
                    "WARN" if baseline else "FAIL",
                    "claude-surface-governance.existence",
                    f"runtime surfaces absent under {live_root}: "
                    + "; ".join(missing_runtime)
                    + (" (baseline profile — this control plane's surfaces, not"
                       " required of every install)" if baseline else ""),
                )
            )
        if missing_other:
            results.append(
                _make_result(
                    "WARN",
                    "claude-surface-governance.existence",
                    f"non-runtime surfaces missing under {live_root}: " + "; ".join(missing_other),
                )
            )
        if not missing_runtime and not missing_other:
            results.append(
                _make_result(
                    "OK",
                    "claude-surface-governance.existence",
                    f"all declared surfaces exist under {live_root}",
                )
            )

    # (d) compat surfaces must point at a canonical owner, not themselves.
    compat_findings = check_compat_ownership(payload=payload)
    if compat_findings:
        results.append(
            _make_result(
                "FAIL",
                "claude-surface-governance.compat-ownership",
                "; ".join(compat_findings),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-surface-governance.compat-ownership",
                "every compatibility surface names a canonical owner distinct from itself",
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
