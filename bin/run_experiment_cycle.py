#!/usr/bin/env python3
"""run_experiment_cycle — one round of the weekly experiment lane.

Mirrors self_harness_cycle.py's shape and safety posture, adapted to a narrower job:
this script EXECUTES an already-filed, already-frozen experiment (see
agentica_core/experiments.py) — it never designs a new hypothesis on its own. Ablation
runs cost real tokens and self-harness's whole precedent is "dark by default, cannot
approve/reject/apply anything autonomously"; this script inherits that posture rather
than re-deriving it.

Safety posture, same order self_harness_cycle.py enforces it in:
  1. DARK BY DEFAULT — EXPERIMENT_LANE_ENABLED=true (or --force) required; otherwise
     exits having done nothing.
  2. Spacing guard — skips when a round ran in the last MIN_ROUND_SPACING_HOURS (weekly
     default). A guard exit writes NOTHING to experiment_lineage.jsonl — logging it would
     make hours_since_last_round measure time since the skip itself, so the very next
     check would see a fresh "round" seconds old and skip again forever (see
     experiment_lineage.py's DECISIONS comment for the same rule stated there).
  3. No pending experiment — a round that ran (passed the spacing guard) but found
     nothing frozen and unverdicted in EXPERIMENTS.jsonl DOES get a lineage row
     (decision=skipped_no_candidate) — the round spent its slot, same as self-harness's
     no_candidates round.
  4. Execution — invokes context-ablation's existing harness (build_arms.py +
     run_ablation.py) against a PRE-BUILT config. CONTRACT: the pending experiment's
     `arm` field must be the filesystem path to a run_ablation.py-compatible config JSON
     (scenarios + checks already designed, arms already built via context-ablation's own
     workflow steps 1-3). This script does not synthesize ablation scenarios from a
     hypothesis string — scenario design is a model's job done once at filing time, not
     a mechanical one repeated every weekly cycle.
  5. Recording — the verdict goes through experiments.record_verdict, which owns the
     escalation decision (INCONCLUSIVE twice on the same hypothesis, or a guardrail
     violation) into PROPOSED_BACKLOG.json. This script never writes there directly.

Usage:
  python3 bin/run_experiment_cycle.py --dry-run   # full round, writes nothing
  python3 bin/run_experiment_cycle.py --force     # supervised round, bypasses enable/spacing
  python3 bin/run_experiment_cycle.py             # honours EXPERIMENT_LANE_ENABLED (default: off)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))
_OS_ROOT = Path(__file__).resolve().parents[1]
if str(_OS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OS_ROOT))

from agentica_core import experiment_lineage, experiments  # noqa: E402

# Same WEEKLY rationale as self_harness_cycle.py's MIN_ROUND_SPACING_HOURS: the cadence
# is this loop's own property, enforced here, not a fact about whatever schedule invokes it.
MIN_ROUND_SPACING_HOURS = float(os.environ.get("EXPERIMENT_LANE_MIN_SPACING_HOURS") or 168)
_ABLATION_TIMEOUT_S = 1800  # context-ablation's own per-run timeouts are inside this box

_CONTEXT_ABLATION_RUN = (
    Path.home() / ".claude" / "skills" / "context-ablation" / "scripts" / "run_ablation.py"
)


def enabled() -> bool:
    return (os.environ.get("EXPERIMENT_LANE_ENABLED") or "false").lower() == "true"


def _invoke_ablation(pending: dict, timeout_s: int = _ABLATION_TIMEOUT_S) -> dict:
    """Real implementation — see module docstring's `arm`-field contract.

    Returns {"verdict": <one of experiments.VERDICTS>, "evidence": str,
    "guardrail_violated": bool}. `guardrail_violated` is always False here: there is no
    mechanical way to check a prose guardrail (e.g. "no regression in
    Frustration_Signals") against an ablation run's pass/fail rates — that assessment is
    a human/reviewer judgment call today, not something this script infers.
    """
    config_path = Path(str(pending.get("arm", "")))
    if not config_path.is_file():
        raise FileNotFoundError(
            f"experiment {pending.get('experiment_id')}'s arm config not found: {config_path} "
            "(the `arm` field must be a path to a run_ablation.py config — see module docstring)"
        )
    workspace = Path(tempfile.mkdtemp(prefix=f"experiment-{pending.get('experiment_id', 'x')}-"))
    proc = subprocess.run(
        [sys.executable, str(_CONTEXT_ABLATION_RUN), str(config_path), "--out", str(workspace)],
        capture_output=True, text=True, timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"run_ablation.py exited {proc.returncode}: {proc.stderr[-500:]}")
    results_path = workspace / "results.json"
    if not results_path.exists():
        raise RuntimeError("run_ablation.py produced no results.json")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    scenarios = results.get("scenarios") or {}
    first = next(iter(scenarios.values()), None)
    if not first or "verdict" not in first:
        raise RuntimeError("run_ablation.py produced no scenario verdict")
    evidence = f"control={first.get('control')} ablated={first.get('ablated')} workspace={workspace}"
    return {"verdict": first["verdict"], "evidence": evidence, "guardrail_violated": False}


def run_round(
    *,
    invoke_ablation_fn: Callable[[dict], dict] = _invoke_ablation,
    lineage_path: Optional[Path] = None,
    experiments_path: Optional[Path] = None,
    backlog_path: Optional[Path] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """One full round. Returns a summary dict (also the CLI's stdout payload)."""
    if not (dry_run or force or enabled()):
        return {"ran": False, "reason": "EXPERIMENT_LANE_ENABLED is off (dark by default)"}
    if not (dry_run or force):
        h = experiment_lineage.hours_since_last_round(lineage_path)
        if h is not None and h < MIN_ROUND_SPACING_HOURS:
            return {"ran": False, "reason": f"last round {h:.1f}h ago < {MIN_ROUND_SPACING_HOURS}h spacing"}

    round_no = 1 + max(
        (e.get("round", 0) for e in experiment_lineage.iter_entries(lineage_path)), default=0
    )

    pending = experiments.next_pending(experiments_path)
    if pending is None:
        reason = "no pending experiment"
        if not dry_run:
            experiment_lineage.append_entry(
                {"round": round_no, "experiment_id": None, "decision": "skipped_no_candidate",
                 "reason": reason},
                lineage_path,
            )
        return {"ran": True, "round": round_no, "experiment_id": None, "reason": reason}

    experiment_id = pending["experiment_id"]
    try:
        result = invoke_ablation_fn(pending)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        reason = f"ablation invocation failed: {exc}"
        if not dry_run:
            experiment_lineage.append_entry(
                {"round": round_no, "experiment_id": experiment_id, "decision": "error",
                 "reason": reason},
                lineage_path,
            )
        return {"ran": True, "round": round_no, "experiment_id": experiment_id, "reason": reason}

    if not dry_run:
        recorded = experiments.record_verdict(
            experiment_id, result["verdict"], result["evidence"],
            result.get("guardrail_violated", False), experiments_path, backlog_path,
        )
        experiment_lineage.append_entry(
            {"round": round_no, "experiment_id": experiment_id,
             "decision": "ran" if recorded else "error",
             "reason": "" if recorded else "record_verdict found no open filed row"},
            lineage_path,
        )

    return {"ran": True, "round": round_no, "experiment_id": experiment_id, "verdict": result["verdict"]}


def main() -> int:
    ap = argparse.ArgumentParser(description="One round of the weekly experiment lane.")
    ap.add_argument("--dry-run", action="store_true", help="full round, write nothing")
    ap.add_argument("--force", action="store_true", help="supervised run: bypass enable/spacing guards")
    args = ap.parse_args()

    summary = run_round(dry_run=args.dry_run, force=args.force)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
