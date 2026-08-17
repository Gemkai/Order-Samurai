"""Backlog item 11: verify the Claude doctor reports EFFECTIVE runtime state.

A subtle "does the doctor lie" check. Doctor output must reflect effective
runtime state (which gateway lanes are live, which optional MCP servers are
disabled by policy) rather than stale or misleading raw counts. Consumes
config/claude_anti_drift_policy.json (the doctor-must-report-effective-state
rule) and confirms the live surface declarations are internally coherent.

This is read-only and conservative. It NEVER executes the live doctor. Where a
property cannot be mechanically verified read-only (e.g. whether the doctor's
prose actually summarizes effective gateway state, or whether the canonical
doctor and its compatibility shim emit equivalent high-level status at runtime),
it emits an honor-system OK row rather than fabricating a check -- mirroring
execution/verify_claude_promotion_policy.py.

Acceptance (mechanically checked where possible, honor-system otherwise):
- doctor reports effective gateway lanes: the doctor documentation entrypoint
  (commands/doctor.md) is PRESENT under the runtime root and the anti-drift
  policy references a doctor runtime artifact. Missing entrypoint = WARN.
  The live doctor is never executed.
- disabled-by-policy optional servers do not surface as broken active
  infrastructure: if mcp.json is parseable and carries disabled/enabled
  metadata, the disabled set must be a coherent subset of the declared servers.
  No such metadata => honor-system OK row.
- canonical doctor and compatibility shim produce equivalent high-level status:
  if the surface matrix declares both a canonical doctor entrypoint and a
  compatibility shim, both must be declared with the shim owned by the same
  owner as the canonical (structural). Runtime status equivalence itself is
  honor-system. A declared compat shim absent under the runtime root is a
  genuine structural contradiction and FAILs.

FAIL is reserved for a genuine structural contradiction (the surface matrix
declares a doctor entrypoint -- the compat shim -- that does not exist under the
runtime root, or mcp.json disables a server it never declares). Everything else
is OK/WARN/honor-system. Missing runtime root or artifacts WARN, never crash.
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
    ANTI_DRIFT_POLICY_PATH,
    BASELINE_PROFILE,
    SURFACE_MATRIX_PATH,
    audit_profile,
    runtime_root,
)

# The operator-facing doctor documentation entrypoint. Its absence is a WARN
# (guidance, not executable infrastructure) -- never a FAIL.
DOCTOR_COMMAND_ENTRYPOINT = "commands/doctor.md"

# The anti-drift rule that mandates effective-state doctor reporting.
DOCTOR_EFFECTIVE_STATE_RULE_ID = "doctor-must-report-effective-state"

# mcp.json server-map keys, in preference order.
MCP_SERVER_MAP_KEYS = ("mcpServers", "servers")
# Top-level disabled-list keys, if a deployment uses one instead of per-server flags.
MCP_DISABLED_LIST_KEYS = ("disabled", "disabledServers", "disabled_servers")


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def policy_references_doctor_artifact(*, payload: dict) -> bool:
    """True if any anti-drift rule references a doctor runtime artifact."""
    for rule in payload.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        for artifact in rule.get("expectedRuntimeArtifacts", []) or []:
            if "doctor" in str(artifact).lower():
                return True
    return False


def find_effective_state_rule(*, payload: dict) -> dict | None:
    for rule in payload.get("rules", []) or []:
        if isinstance(rule, dict) and rule.get("id") == DOCTOR_EFFECTIVE_STATE_RULE_ID:
            return rule
    return None


def collect_server_disable_state(
    *, mcp_payload: dict
) -> tuple[set[str], set[str], bool, list[str], list[str]]:
    """Return (declared_servers, disabled, metadata_present, malformed, unknown_disabled).

    disabled: servers marked disabled via a per-server ``disabled: true`` /
    ``enabled: false`` flag or a top-level disabled list.
    malformed: servers whose disabled/enabled flag is not a boolean.
    unknown_disabled: names in a top-level disabled list that are not declared
    servers -- an incoherent (misleading) declaration.
    """
    servers: dict = {}
    for key in MCP_SERVER_MAP_KEYS:
        candidate = mcp_payload.get(key)
        if isinstance(candidate, dict):
            servers = candidate
            break

    declared = set(servers)
    disabled: set[str] = set()
    malformed: list[str] = []
    metadata_present = False

    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if "disabled" in cfg:
            metadata_present = True
            value = cfg["disabled"]
            if isinstance(value, bool):
                if value:
                    disabled.add(name)
            else:
                malformed.append(name)
        elif "enabled" in cfg:
            metadata_present = True
            value = cfg["enabled"]
            if isinstance(value, bool):
                if not value:
                    disabled.add(name)
            else:
                malformed.append(name)

    unknown_disabled: list[str] = []
    for key in MCP_DISABLED_LIST_KEYS:
        top = mcp_payload.get(key)
        if isinstance(top, list):
            metadata_present = True
            for entry in top:
                name = str(entry)
                disabled.add(name)
                if name not in declared:
                    unknown_disabled.append(name)

    return declared, disabled, metadata_present, sorted(malformed), sorted(unknown_disabled)


def find_doctor_surfaces(*, matrix_payload: dict) -> dict[str, dict | None]:
    """Locate the canonical doctor entrypoint, the compat shim, and the doc
    entrypoint among the surface-matrix declarations."""
    canonical: dict | None = None
    compat: dict | None = None
    doc: dict | None = None
    for surface in matrix_payload.get("surfaces", []) or []:
        if not isinstance(surface, dict):
            continue
        path = str(surface.get("path") or "").strip()
        role = str(surface.get("role") or "").strip()
        contract = str(surface.get("discoverabilityContract") or "").strip().lower()
        if "doctor" not in path.lower() and "doctor" not in contract:
            continue
        if role == "compatibility":
            compat = surface
        elif path.lower().endswith(".md"):
            doc = surface
        elif "canonical" in contract or path.lower().endswith(".py"):
            canonical = surface
    return {"canonical": canonical, "compat": compat, "doc": doc}


def run_checks(
    policy_path: Path = ANTI_DRIFT_POLICY_PATH,
    matrix_path: Path = SURFACE_MATRIX_PATH,
    runtime_root_dir: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    runtime = runtime_root_dir if runtime_root_dir is not None else runtime_root()
    runtime_present = runtime.is_dir()

    policy_rows, references_doctor, policy_loaded = _check_anti_drift_policy(policy_path)
    results.extend(policy_rows)
    if not policy_loaded:
        # An unreadable anti-drift policy short-circuits the whole verifier: every
        # clause below judges the runtime AGAINST that policy, so continuing would
        # report conclusions drawn from a document that was never read.
        return results
    results.extend(_check_doctor_entrypoint(runtime, runtime_present, references_doctor))
    results.extend(_check_disabled_mcp_servers(matrix_path, runtime, runtime_present))
    results.extend(_check_canonical_doctor_and_shim(matrix_path, runtime, runtime_present))
    results.extend(_check_tolerated_runtime_doctor_absence())
    return results


def _check_anti_drift_policy(
    policy_path: Path,
) -> tuple[list[dict[str, str]], bool, bool]:
    """The anti-drift policy loads and mandates the effective-state rule.

    Also returns whether the policy references a doctor runtime artifact — the
    one fact the entrypoint clause needs from this one, passed explicitly
    rather than left as a shared local. The third element reports whether the
    policy loaded at all; a False short-circuits run_checks, preserving the
    early return this clause used to perform directly."""
    results: list[dict[str, str]] = []
    # --- anti-drift policy load + effective-state rule -----------------------
    policy_payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(
            _make_result("FAIL", "claude_anti_drift_policy.json", policy_error)
        )
        return results, False, False
    payload = policy_payload or {}

    effective_rule = find_effective_state_rule(payload=payload)
    references_doctor = policy_references_doctor_artifact(payload=payload)
    if effective_rule is None:
        results.append(
            _make_result(
                "WARN",
                "anti_drift.doctor_effective_state_rule",
                f"anti-drift policy does not declare a '{DOCTOR_EFFECTIVE_STATE_RULE_ID}' "
                "rule; doctor truthfulness is not mandated by contract",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "anti_drift.doctor_effective_state_rule",
                "anti-drift policy mandates the doctor summarize effective gateway "
                "and MCP activation state, not misleading raw counts",
            )
        )

    return results, references_doctor, True


def _check_doctor_entrypoint(runtime: Path, runtime_present: bool, references_doctor: bool) -> list[dict[str, str]]:
    """Bullet 1: the doctor entrypoint exists and the policy references it."""
    results: list[dict[str, str]] = []
    # --- bullet 1: doctor entrypoint present & referenced --------------------
    if not runtime_present:
        results.append(
            _make_result(
                "WARN",
                "doctor.entrypoint",
                f"runtime root {runtime} is absent; cannot confirm the doctor "
                "entrypoint (read-only, not fabricated)",
            )
        )
    else:
        entrypoint = runtime / DOCTOR_COMMAND_ENTRYPOINT
        if not entrypoint.is_file():
            results.append(
                _make_result(
                    "WARN",
                    "doctor.entrypoint",
                    f"doctor entrypoint {DOCTOR_COMMAND_ENTRYPOINT} is missing under "
                    "the runtime root",
                )
            )
        elif not references_doctor:
            results.append(
                _make_result(
                    "WARN",
                    "doctor.entrypoint",
                    f"doctor entrypoint {DOCTOR_COMMAND_ENTRYPOINT} is present but the "
                    "anti-drift policy references no doctor runtime artifact",
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "doctor.entrypoint",
                    f"doctor entrypoint {DOCTOR_COMMAND_ENTRYPOINT} is present and "
                    "referenced by the anti-drift policy (live doctor not executed)",
                )
            )

    return results


def _check_disabled_mcp_servers(matrix_path: Path, runtime: Path, runtime_present: bool) -> list[dict[str, str]]:
    """Bullet 2: disabled optional MCP servers are a coherent subset."""
    results: list[dict[str, str]] = []
    # --- bullet 2: disabled optional MCP servers are a coherent subset -------
    if not runtime_present:
        results.append(
            _make_result(
                "WARN",
                "doctor.mcp_disabled_subset",
                f"runtime root {runtime} is absent; cannot inspect mcp.json",
            )
        )
    else:
        mcp_path = runtime / "mcp.json"
        mcp_payload, mcp_error = _load_json(mcp_path)
        if mcp_error:
            results.append(
                _make_result(
                    "WARN",
                    "doctor.mcp_disabled_subset",
                    f"mcp.json is {mcp_error}; cannot verify disabled-server coherence",
                )
            )
        else:
            declared, disabled, metadata_present, malformed, unknown = (
                collect_server_disable_state(mcp_payload=mcp_payload or {})
            )
            if not metadata_present:
                results.append(
                    _make_result(
                        "OK",
                        "doctor.mcp_disabled_subset",
                        "mcp.json declares no disabled/enabled metadata; that disabled "
                        "servers do not surface as broken active infra is honor-system "
                        "(no metadata to mechanically verify)",
                    )
                )
            elif unknown:
                results.append(
                    _make_result(
                        "FAIL",
                        "doctor.mcp_disabled_subset",
                        "mcp.json disables server(s) it never declares: "
                        + ", ".join(unknown),
                    )
                )
            elif malformed:
                results.append(
                    _make_result(
                        "WARN",
                        "doctor.mcp_disabled_subset",
                        "mcp.json has non-boolean disabled/enabled flags on: "
                        + ", ".join(malformed),
                    )
                )
            else:
                results.append(
                    _make_result(
                        "OK",
                        "doctor.mcp_disabled_subset",
                        f"{len(disabled)} of {len(declared)} MCP servers are disabled by "
                        "policy; the disabled set is a coherent subset of declared servers",
                    )
                )

    return results


def _check_canonical_doctor_and_shim(matrix_path: Path, runtime: Path, runtime_present: bool) -> list[dict[str, str]]:
    """Bullet 3: canonical doctor plus compat shim (structural + honor)."""
    results: list[dict[str, str]] = []
    # --- bullet 3: canonical doctor + compat shim (structural + honor) -------
    matrix_payload, matrix_error = _load_json(matrix_path)
    if matrix_error:
        results.append(
            _make_result("WARN", "claude_surface_matrix.json", matrix_error)
        )
    else:
        surfaces = find_doctor_surfaces(matrix_payload=matrix_payload or {})
        canonical = surfaces["canonical"]
        compat = surfaces["compat"]

        if canonical is not None and compat is not None:
            canonical_owner = str(canonical.get("owner") or "").strip()
            compat_owner = str(compat.get("owner") or "").strip()
            if canonical_owner and compat_owner and canonical_owner != compat_owner:
                results.append(
                    _make_result(
                        "WARN",
                        "doctor.canonical_vs_compat",
                        f"compat shim {compat.get('path')!r} owned by "
                        f"{compat_owner!r} but canonical doctor {canonical.get('path')!r} "
                        f"owned by {canonical_owner!r}; they should share an owner",
                    )
                )
            else:
                results.append(
                    _make_result(
                        "OK",
                        "doctor.canonical_vs_compat",
                        f"canonical doctor {canonical.get('path')!r} and compat shim "
                        f"{compat.get('path')!r} share owner {canonical_owner!r}; "
                        "runtime status equivalence itself is honor-system",
                    )
                )

            # Mechanical: a declared compat shim must exist under the runtime root.
            compat_path = str(compat.get("path") or "").strip()
            if not runtime_present:
                results.append(
                    _make_result(
                        "WARN",
                        "doctor.compat_shim_present",
                        f"runtime root {runtime} is absent; cannot confirm compat shim "
                        f"{compat_path}",
                    )
                )
            elif compat_path and not (runtime / compat_path).exists():
                # The compat shim is this control plane's own file. On the baseline
                # profile the target is any Claude Code install, which never had one
                # — see claude_runtime_target.audit_profile.
                baseline = audit_profile() == BASELINE_PROFILE
                results.append(
                    _make_result(
                        "WARN" if baseline else "FAIL",
                        "doctor.compat_shim_present",
                        f"surface matrix declares doctor compat shim {compat_path} "
                        "but it is absent under the runtime root"
                        + (" (baseline profile — not required of every install)"
                           if baseline else ""),
                    )
                )
            else:
                results.append(
                    _make_result(
                        "OK",
                        "doctor.compat_shim_present",
                        f"declared doctor compat shim {compat_path} exists under the "
                        "runtime root",
                    )
                )
        else:
            results.append(
                _make_result(
                    "OK",
                    "doctor.canonical_vs_compat",
                    "surface matrix declares fewer than two doctor surfaces "
                    "(canonical+compat); equivalence check is honor-system only",
                )
            )

    return results


def _check_tolerated_runtime_doctor_absence() -> list[dict[str, str]]:
    """Honor-system: a runtime doctor script under scripts/ may or may not exist."""
    results: list[dict[str, str]] = []
    # --- honor-system: tolerated absence of the runtime doctor script --------
    results.append(
        _make_result(
            "OK",
            "doctor.runtime_script_honor_system",
            "a runtime doctor script under scripts/ may or may not exist; its "
            "presence and effective-state prose are honor-system (the live doctor "
            "is never executed by this read-only verifier)",
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
