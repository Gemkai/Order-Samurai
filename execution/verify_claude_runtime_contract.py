#!/usr/bin/env python3
"""Aggregate the foundational Claude runtime verifiers into one gate.

Backlog item 6. Rather than shelling into the live ~/.claude doctor (heavy, and
it would couple this repo to the runtime's process contract), this faithfully
reproduces the runtime contract by running the sibling foundational verifiers
in-process and rolling their worst status per contract area into one verdict —
plus a direct required-runtime-artifacts existence check.

Read-only. Emits the same {status,label,detail} rows + summarize()/main()
convention every verify_claude_* module uses, so execution/doctor.py can consume
it uniformly (a single FAIL row iff any aggregated area FAILs).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# Allow direct `python3 execution/verify_claude_runtime_contract.py` (repo root
# not on sys.path when run as a script), mirroring the sibling verifiers.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import (  # type: ignore[import-not-found]  # noqa: E402
    ANTI_DRIFT_POLICY_PATH,
    BASELINE_PROFILE,
    audit_profile,
    runtime_root,
)

# Foundational contract areas, each backed by a sibling verifier module. Order
# is the backlog's foundational sequence. A module that is absent (not yet
# implemented) is reported as an advisory WARN, never a crash — so this gate
# degrades gracefully as the pack fills in.
FOUNDATIONAL_VERIFIERS = (
    ("path_authority", "execution.verify_claude_path_authority"),
    ("hook_contract", "execution.verify_claude_hook_contract"),
    ("mcp_contract", "execution.verify_claude_mcp_contract"),
    ("generated_truth", "execution.verify_claude_generated_truth"),
    ("runtime_coupling", "execution.verify_claude_runtime_coupling"),
)

# Required runtime artifacts the contract asserts must exist under the runtime
# root. Absent = FAIL (the runtime is structurally incomplete), mirroring the
# repo-side verify_runtime_contract's required-artifacts gate.
REQUIRED_RUNTIME_ARTIFACTS = (
    "settings.json",
    "mcp.json",
    "commands/doctor.md",
    "CLAUDE.md",
    "AGENTS.md",
)

_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}
_UNRANK = {0: "OK", 1: "WARN", 2: "FAIL"}

# Sibling run_checks() functions don't all name their root-override kwarg
# `runtime_root_dir` (verify_claude_path_authority uses `runtime_root_path`;
# verify_claude_runtime_coupling uses `root`). Without this map, calling with
# `runtime_root_dir=` raises TypeError on those two, which the fallback below
# catches by calling `run()` with no args at all -- silently scanning the live
# default runtime instead of the caller-supplied root.
_ROOT_KWARG_BY_MODULE = {
    "execution.verify_claude_path_authority": "runtime_root_path",
    "execution.verify_claude_runtime_coupling": "root",
}


def _roll_up(rows: list[dict[str, str]]) -> str:
    """Worst status across a verifier's rows."""
    worst = 0
    for row in rows:
        worst = max(worst, _RANK.get(row["status"], 0))
    return _UNRANK[worst]


def _worst_detail(rows: list[dict[str, str]], status: str) -> str:
    hits = [r for r in rows if r["status"] == status]
    if not hits:
        return "no rows"
    shown = "; ".join(f"{r['label']}: {r['detail']}" for r in hits[:3])
    extra = f" (+{len(hits) - 3} more)" if len(hits) > 3 else ""
    return shown + extra


def run_checks(*, runtime_root_dir: Path | None = None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    runtime = runtime_root_dir or runtime_root()

    if not ANTI_DRIFT_POLICY_PATH.exists():
        return [_make_result("FAIL", "claude_runtime_contract.policy",
                             f"anti-drift policy missing: {ANTI_DRIFT_POLICY_PATH}")]

    # 1. Required runtime artifacts.
    if not runtime.exists():
        results.append(_make_result(
            "WARN", "claude_runtime_contract.artifacts",
            f"runtime root missing on this machine: {runtime} — artifact checks skipped"))
    else:
        missing = [a for a in REQUIRED_RUNTIME_ARTIFACTS if not (runtime / a).exists()]
        if missing:
            # REQUIRED_RUNTIME_ARTIFACTS describes this control plane's own
            # artifacts (mcp.json, commands/doctor.md, CLAUDE.md, AGENTS.md). On
            # the baseline profile the target is any Claude Code install, which has
            # none of them by default — see claude_runtime_target.audit_profile.
            baseline = audit_profile() == BASELINE_PROFILE
            results.append(_make_result(
                "WARN" if baseline else "FAIL", "claude_runtime_contract.artifacts",
                f"runtime artifacts absent: {', '.join(missing)}"
                + (" (baseline profile — not required of every install)"
                   if baseline else " — required by this control plane")))
        else:
            results.append(_make_result(
                "OK", "claude_runtime_contract.artifacts",
                f"all {len(REQUIRED_RUNTIME_ARTIFACTS)} required runtime artifacts present"))

    # 2. Roll up each foundational verifier.
    for area, module_name in FOUNDATIONAL_VERIFIERS:
        label = f"claude_runtime_contract.{area}"
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            results.append(_make_result(
                "WARN", label,
                f"foundational verifier not yet implemented ({module_name}) — advisory"))
            continue

        run = getattr(module, "run_checks", None)
        if run is None:
            results.append(_make_result(
                "WARN", label, f"{module_name} exposes no run_checks() — advisory"))
            continue

        try:
            if runtime_root_dir is not None:
                kwarg = _ROOT_KWARG_BY_MODULE.get(module_name, "runtime_root_dir")
                rows = run(**{kwarg: runtime})
            else:
                rows = run()
        except TypeError:
            # Sibling may not accept a root override at all; fall back to its default.
            rows = run()
        except Exception as exc:  # a verifier crash is itself a contract breach
            results.append(_make_result("FAIL", label, f"{module_name} raised: {exc}"))
            continue

        status = _roll_up(rows)
        detail = (f"{area} clean ({len(rows)} checks)" if status == "OK"
                  else _worst_detail(rows, status))
        results.append(_make_result(status, label, detail))

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
