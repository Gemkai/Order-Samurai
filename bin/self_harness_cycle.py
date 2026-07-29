#!/usr/bin/env python3
"""self_harness_cycle — one round of bounded harness self-improvement (M4).

Algorithm 1 of Self-Harness (arXiv 2606.09498), adapted to this stack:

  evidence bundle -> K candidate edits -> structural rejection -> pinned validation
  -> per-task acceptance rule -> human delivery (HITL) -> lineage ledger

Everything a candidate can be is a set of {surface_key: int} edits to the declared editable
surface. That is deliberately NARROWER than the paper's free-form harness diffs: a values-only
surface admits values-only proposals, and "the proposer structurally cannot touch anything else"
beats "we check that it didn't". Diff-shaped proposals become necessary only when instruction
surfaces arrive (v2).

Safety posture, in the order the run enforces it:
  1. DARK BY DEFAULT — SELF_HARNESS_ENABLED=true (or --force for a supervised run) is required;
     otherwise the cycle exits 0 having done nothing.
  2. Budget + spacing guards — skips when the meditation budget ledger is exhausted or a round
     ran in the last 20 hours.
  3. Structural rejection — unknown key, out-of-bounds value, wrong type, empty or duplicate
     edit sets: rejected before a single evaluation is spent, logged to the lineage ledger.
  4. Pinned validation — candidates are evaluated via OS_HARNESS_<KEY> env overrides (the escape
     hatch harness_config was designed with), never by editing files. No worktree is needed
     because nothing writes; the surface file the suite measures stays byte-identical throughout.
  5. Acceptance — PER-TASK no-regression on every task (the video's third candidate broke one
     task while the aggregate barely moved), then the paper's split rule:
     delta_in >= 0 AND delta_out >= 0 AND max(delta) > 0 on split means.
  6. Delivery is a PROPOSAL, never an application — the winning candidate becomes a ready-to-
     review file under state/proposed_surface_edits/ plus a pending HITL queue item. No git
     mutation, no auto-merge, no restart. A human applies the one-file edit or doesn't.

The proposer never sees held-out task ids, contents, or results. Expect rejection: the paper
retains ~3-4 edits per 11-20 rounds; a round that accepts nothing is the loop working.

Rival adversarial verification is deliberately NOT spawned here: rival is a Claude subagent that
exists inside sensei-cycle's orchestration. When SELF_HARNESS_ENABLED goes live via the sensei
stage (plan step 4.2), sensei runs rival (mode:pre) over the HITL entry before a human sees it.
The ledger row carries everything rival needs.

Usage:
  python3 bin/self_harness_cycle.py --dry-run     # full round, writes nothing (guards bypassed)
  python3 bin/self_harness_cycle.py --force       # supervised round, writes HITL + ledger
  python3 bin/self_harness_cycle.py               # honours SELF_HARNESS_ENABLED (default: off)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))
_OS_ROOT = Path(__file__).resolve().parents[1]
if str(_OS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OS_ROOT))

from agentica_core import harness_config, harness_lineage  # noqa: E402
from agentica_core.atomic import atomic_json_write  # noqa: E402
from bin import harness_eval_runner as eval_runner  # noqa: E402

_STATE = _OS_ROOT / "state"
_CLUSTERS = _STATE / "weakness_clusters.json"
_HITL = _STATE / "hitl_queue.json"
_BUDGET = _STATE / "budget_ledger.json"
_PROPOSED_DIR = _STATE / "proposed_surface_edits"

K_CANDIDATES = 3
MIN_ROUND_SPACING_HOURS = 20
_PROPOSER_TIMEOUT_S = 300


# --- guards ------------------------------------------------------------------------------------

def enabled() -> bool:
    return (os.environ.get("SELF_HARNESS_ENABLED") or "false").lower() == "true"


def budget_exhausted() -> bool:
    """True when the meditation daily budget is spent — this cycle rides the same allowance."""
    try:
        d = json.loads(_BUDGET.read_text(encoding="utf-8"))
        return float(d.get("spent_usd", 0)) >= float(d.get("daily_limit_usd", 0))
    except (OSError, ValueError):
        return False  # a missing/broken ledger must not silently disable the loop forever


def hours_since_last_round(path: Optional[Path] = None) -> Optional[float]:
    last = None
    for e in harness_lineage.iter_entries(path):
        last = e.get("ts") or last
    if not last:
        return None
    try:
        then = datetime.fromisoformat(last)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


# --- evidence bundle ---------------------------------------------------------------------------

def build_evidence_bundle(baseline: dict, ledger_path: Optional[Path] = None) -> dict:
    """What the proposer may see: held-in failures, weakness clusters, behaviours to preserve,
    prior attempts. HELD-OUT NEVER APPEARS HERE — not ids, not counts, not names."""
    tasks = {t["id"]: t for t in eval_runner.load_tasks()}
    held_in = set(eval_runner.load_split()["held_in"])

    red, green = [], []
    for tid, r in baseline["results"].items():
        if tid not in held_in:
            continue
        if r["fraction"] < 1.0:
            t = tasks[tid]
            red.append({
                "task_id": tid,
                "kind": r["kind"],
                "fraction": r["fraction"],
                "description": t["description"],
                "grounded_in": t["grounded_in"],
                "candidate_surface_keys": t["sensitive_to"],
            })
        else:
            green.append({"task_id": tid, "description": tasks[tid]["description"]})

    clusters = []
    try:
        payload = json.loads(_CLUSTERS.read_text(encoding="utf-8"))
        clusters = [c for c in payload.get("clusters", []) if c.get("actionable")][:3]
    except (OSError, ValueError):
        pass

    surface = harness_config.load_surface()
    return {
        "editable_surface": {
            k: {kk: spec[kk] for kk in ("value", "type", "min", "max", "note") if kk in spec}
            for k, spec in surface["values"].items()
        },
        "failing_held_in_tasks": red,
        "actionable_weakness_clusters": clusters,
        "passing_behaviours_to_preserve": green,
        "previously_attempted_edits": harness_lineage.recent_attempts(10, ledger_path),
    }


# --- proposer ----------------------------------------------------------------------------------

_PROPOSER_PROMPT = """You are the harness-proposal stage of a bounded self-improvement loop \
(Self-Harness, arXiv 2606.09498). A fixed evaluation suite has failing tasks; you may propose \
edits ONLY to the declared editable surface below. Anything else is out of bounds and will be \
structurally rejected.

EVIDENCE (JSON):
{bundle}

Propose up to {k} MATERIALLY DISTINCT candidates. Each candidate: the smallest edit that could \
address a failing task's mechanism. Do not re-propose previously rejected edits. Respect each \
key's min/max. Prefer one-key edits.

Reply with ONLY a JSON array (no prose, no fences):
[{{"edits": {{"<surface_key>": <int>}},
   "audit": {{"target_pattern": "<failing task/mechanism>", "expected_effect": "<one line>",
              "regression_risks": ["<one line each>"]}},
   "predicted_impact": {{"expected_fixes": ["<task ids>"], "at_risk": ["<task ids>"]}}}}]
"""


def spawn_proposer(bundle: dict, k: int = K_CANDIDATES) -> list[dict]:
    """Default proposer: one headless `claude -p` spawn. Tests inject their own propose_fn.

    RAISES on any failure to obtain a parseable proposal — a proposer that quietly returns []
    makes every round read as "the model proposed nothing", when the truth may be "the CLI could
    not even run" (observed 2026-07-16: a nested spawn failing OAuth produced exactly that silent
    zero). Unparseable output is a failure, never data — the same local_guards discipline, applied
    to the cloud CLI."""
    prompt = _PROPOSER_PROMPT.format(bundle=json.dumps(bundle, indent=1), k=k)
    proc = subprocess.run(
        ["claude", "-p", prompt], capture_output=True, text=True, timeout=_PROPOSER_TIMEOUT_S,
    )
    text = proc.stdout or ""
    if proc.returncode != 0:
        raise RuntimeError(f"proposer CLI exited {proc.returncode}: {(text or proc.stderr)[:300].strip()}")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise RuntimeError(f"proposer returned no JSON array: {text[:300].strip()!r}")
    try:
        out = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise RuntimeError(f"proposer JSON unparseable: {exc}") from exc
    if not isinstance(out, list):
        raise RuntimeError(f"proposer returned {type(out).__name__}, expected a JSON array")
    return out


# --- structural rejection ----------------------------------------------------------------------

def structural_check(candidate: object, surface: dict) -> Optional[str]:
    """Reject-before-evaluate. Returns a reason string, or None when structurally sound."""
    if not isinstance(candidate, dict):
        return "candidate is not an object"
    edits = candidate.get("edits")
    if not isinstance(edits, dict) or not edits:
        return "no edits proposed"
    values = surface["values"]
    for key, val in edits.items():
        if key not in values:
            return f"unknown surface key {key!r} — outside the declared editable surface"
        spec = values[key]
        if not isinstance(val, int) or isinstance(val, bool):
            return f"{key}: value {val!r} is not an int"
        if spec.get("min") is not None and val < spec["min"]:
            return f"{key}: {val} < declared min {spec['min']}"
        if spec.get("max") is not None and val > spec["max"]:
            return f"{key}: {val} > declared max {spec['max']}"
        if val == spec["value"]:
            return f"{key}: {val} equals the current value — not an edit"
    return None


# --- pinned validation -------------------------------------------------------------------------

@contextmanager
def _pinned(edits: dict):
    """Apply candidate values via the OS_HARNESS_<KEY> override channel, restore on exit.
    The surface FILE is never touched — the escape hatch documented in harness_config is the
    whole validation mechanism."""
    saved: dict[str, Optional[str]] = {}
    try:
        for key, val in edits.items():
            env_key = f"OS_HARNESS_{key.upper()}"
            saved[env_key] = os.environ.get(env_key)
            os.environ[env_key] = str(val)
        yield
    finally:
        for env_key, old in saved.items():
            if old is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = old


def evaluate_candidate(edits: dict, run_suite_fn: Callable[[], dict]) -> dict:
    with _pinned(edits):
        return run_suite_fn()


def acceptance(baseline: dict, candidate: dict) -> tuple[bool, str, dict]:
    """The gate. Returns (accepted, reason, deltas).

    Rule 1 (per-task, stricter than the paper): NO individual task's fraction may decrease.
    Rule 2 (the paper's): delta_in >= 0 AND delta_out >= 0 AND max(delta) > 0 on split means.
    """
    per_task: dict[str, list[float]] = {}
    for tid, base in baseline["results"].items():
        cand = candidate["results"].get(tid)
        if cand is None:
            return False, f"candidate run missing task {tid}", {}
        per_task[tid] = [base["fraction"], cand["fraction"]]
        if cand["fraction"] < base["fraction"]:
            return (False,
                    f"per-task regression: {tid} {base['fraction']} -> {cand['fraction']}",
                    {"per_task": per_task})

    d_in = round((candidate["held_in_mean"] or 0) - (baseline["held_in_mean"] or 0), 3)
    d_out = round((candidate["held_out_mean"] or 0) - (baseline["held_out_mean"] or 0), 3)
    deltas = {"held_in_delta": d_in, "held_out_delta": d_out, "per_task": per_task}
    if d_in < 0 or d_out < 0:
        return False, f"split regression: d_in={d_in} d_out={d_out}", deltas
    if max(d_in, d_out) <= 0:
        return False, "no improvement on either split", deltas
    return True, f"improved: d_in={d_in} d_out={d_out}, no task regressed", deltas


# --- delivery ----------------------------------------------------------------------------------

def deliver(winner: dict, deltas: dict, candidate_id: str, dry_run: bool) -> dict:
    """Winner -> ready-to-review proposed surface + pending HITL item. Never applies anything."""
    surface = harness_config.load_surface()
    proposed = json.loads(json.dumps(surface))  # deep copy
    for key, val in winner["edits"].items():
        proposed["values"][key]["value"] = val

    table = ["| task | before | after |", "|---|---|---|"]
    for tid, (b, c) in sorted(deltas["per_task"].items()):
        marker = " **←**" if c != b else ""
        table.append(f"| {tid} | {b} | {c}{marker} |")

    context = (
        f"Self-harness round proposal {candidate_id}. Edits: "
        + ", ".join(f"{k}: {surface['values'][k]['value']} -> {v}" for k, v in winner["edits"].items())
        + f". Splits: held-in delta {deltas['held_in_delta']}, held-out delta {deltas['held_out_delta']}."
        + " Per-task results:\n" + "\n".join(table)
        + f"\n\nReady-to-review surface: state/proposed_surface_edits/{candidate_id}.json —"
        " review, then copy over harness/editable_surface.json, re-run bin/render_surface_env.py,"
        " and restart the governance API. Rival (mode:pre) verification pending sensei wiring."
        f"\nAudit: {json.dumps(winner.get('audit', {}))}"
    )

    item = {
        "id": f"hitl-sh-{hashlib.sha256(json.dumps(winner['edits'], sort_keys=True).encode()).hexdigest()[:8]}",
        "source": "self_harness",
        "tier_assigned": "hitl",
        "status": "pending",
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "approved_at": None, "rejected_at": None, "rejected_reason": None,
        "executing_at": None, "completed_at": None,
        "skill": "self-harness",
        "command": f"surface-edit {json.dumps(winner['edits'], sort_keys=True)}",
        "metric_id": "harness:editable_surface",
        "pillar": "brush",
        "blast_radius": "harness_surface",
        "reversible": True,
        "context": context,
    }

    if not dry_run:
        _PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
        atomic_json_write(_PROPOSED_DIR / f"{candidate_id}.json", proposed)
        try:
            queue = json.loads(_HITL.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            queue = {"schema_version": 1,
                     "created_at": datetime.now(timezone.utc).isoformat(), "items": []}
        items = queue.setdefault("items", [])
        # Idempotent on item id (a hash of the edits): a later round that re-selects the
        # SAME winning edit must not append a duplicate pending proposal. Without this,
        # 34 copies of one {"scout_max_files": 100} edit piled up while awaiting review.
        already = any(isinstance(x, dict) and x.get("id") == item["id"]
                      and x.get("status") == "pending" for x in items)
        if not already:
            items.append(item)
            queue["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_json_write(_HITL, queue)
    return item


# --- the round ---------------------------------------------------------------------------------

def run_round(
    *,
    propose_fn: Callable[[dict], list[dict]] = spawn_proposer,
    run_suite_fn: Callable[[], dict] = eval_runner.run_suite,
    ledger_path: Optional[Path] = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """One full round. Returns a summary dict (also the CLI's stdout payload)."""
    if not (dry_run or force or enabled()):
        return {"ran": False, "reason": "SELF_HARNESS_ENABLED is off (dark by default)"}
    if not (dry_run or force):
        if budget_exhausted():
            return {"ran": False, "reason": "daily budget ledger exhausted"}
        h = hours_since_last_round(ledger_path)
        if h is not None and h < MIN_ROUND_SPACING_HOURS:
            return {"ran": False, "reason": f"last round {h:.1f}h ago < {MIN_ROUND_SPACING_HOURS}h spacing"}

    surface = harness_config.load_surface()
    round_no = 1 + max((e.get("round", 0) for e in harness_lineage.iter_entries(ledger_path)), default=0)

    baseline = run_suite_fn()
    bundle = build_evidence_bundle(baseline, ledger_path)
    if not bundle["failing_held_in_tasks"] and not bundle["actionable_weakness_clusters"]:
        return {"ran": True, "round": round_no, "candidates": 0,
                "reason": "no failing held-in tasks and no actionable clusters — nothing to propose"}

    try:
        candidates = (propose_fn(bundle) or [])[:K_CANDIDATES]
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        # A proposer failure ends the round loudly: distinguishable forever from "the model
        # looked at the evidence and proposed nothing".
        return {"ran": True, "round": round_no, "candidates": 0,
                "proposer_error": str(exc), "accepted": [], "rejected": [], "structural_rejects": []}
    seen_edits: set[str] = set()
    summary = {"ran": True, "round": round_no, "baseline_fingerprint": baseline["harness_fingerprint"],
               "candidates": len(candidates), "accepted": [], "rejected": [], "structural_rejects": []}

    accepted: list[tuple[dict, dict, str]] = []
    for i, cand in enumerate(candidates, start=1):
        cid = f"r{round_no}c{i}"
        reason = structural_check(cand, surface)
        edits_key = json.dumps(cand.get("edits"), sort_keys=True) if isinstance(cand, dict) else ""
        if reason is None and edits_key in seen_edits:
            reason = "duplicate of an earlier candidate this round"
        if reason is not None:
            summary["structural_rejects"].append({"candidate_id": cid, "reason": reason})
            if not dry_run:
                harness_lineage.append_entry({
                    "round": round_no, "candidate_id": cid, "decision": "structural_reject",
                    "reason": reason, "edits": cand.get("edits") if isinstance(cand, dict) else None,
                    "audit": cand.get("audit") if isinstance(cand, dict) else None,
                }, ledger_path)
            continue
        seen_edits.add(edits_key)

        result = evaluate_candidate(cand["edits"], run_suite_fn)
        ok, why, deltas = acceptance(baseline, result)
        row = {
            "round": round_no, "candidate_id": cid,
            "decision": "accepted" if ok else "rejected", "reason": why,
            "edits": cand["edits"], "audit": cand.get("audit", {}),
            "predicted_impact": cand.get("predicted_impact", {}),
            "eval": {k: deltas.get(k) for k in ("held_in_delta", "held_out_delta", "per_task")},
            "baseline_fingerprint": baseline["harness_fingerprint"],
        }
        if not dry_run:
            harness_lineage.append_entry(row, ledger_path)
        (summary["accepted"] if ok else summary["rejected"]).append(
            {"candidate_id": cid, "edits": cand["edits"], "reason": why})
        if ok:
            accepted.append((cand, deltas, cid))

    if accepted:
        # Winner: largest held-out gain (generalisation evidence), tie-broken by held-in.
        winner, deltas, cid = max(
            accepted, key=lambda t: (t[1]["held_out_delta"], t[1]["held_in_delta"]))
        summary["winner"] = deliver(winner, deltas, cid, dry_run)["id"] if not dry_run else cid
        summary["winner_edits"] = winner["edits"]
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="One round of bounded harness self-improvement.")
    ap.add_argument("--dry-run", action="store_true", help="full round, write nothing")
    ap.add_argument("--force", action="store_true", help="supervised run: bypass enable/spacing guards")
    args = ap.parse_args()

    summary = run_round(dry_run=args.dry_run, force=args.force)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
