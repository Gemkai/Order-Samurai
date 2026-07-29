#!/usr/bin/env python3
"""weakness_mining_scout — stage 1 of the self-harness loop (Self-Harness arXiv 2606.09498).

The metric layer already answers "which number is bad". This answers the different question the
harness cannot currently ask: **which recurring agent BEHAVIOUR keeps failing, for one provable
shared cause?** A metric threshold fires on a symptom; a weakness cluster names a mechanism.

Pipeline, per the paper:
  1. Collect failed reflex runs (exec_log.jsonl status error/timeout) inside a day window.
  2. Reconstruct each run's trace from reflex_output.jsonl (stdout lines keyed by metric).
  3. Attribute a failure signature (terminal_cause, causal_status, mechanism) to each.
  4. Cluster by EXACT signature agreement; rank by support; mark actionability.
  5. Atomic-write state/weakness_clusters.json.

Three deliberate deviations from the paper, each toward determinism (documented for review):

  * `terminal_cause` is derived from the record, not judged. The engine already classifies
    error/timeout/quota; asking a 12B model to re-derive what we know is spend without signal.
  * `mechanism` uses a CLOSED vocabulary. The paper clusters on exact signature agreement, which
    presumes the mechanism string repeats. A local 12B emitting free-form slugs produces
    "tool_loop" / "stuck_in_tool_loop" / "repeated_tool_calls" for one mechanism -- every cluster
    lands at count 1 and nothing is ever actionable. A closed label set is what makes exact-match
    clustering work at this model tier. Novel mechanisms fall into `other`, whose examples carry
    the free-text explanation so the vocabulary can be widened by a human.
  * `actionable` is a deterministic map (mechanism -> candidate surface keys), not a judgment.
    The editable surface is six numeric knobs; asking a model "could knob X fix this" invites
    agreeable guessing. The map is honest about the common answer being "no".

Honest gaps: a record whose judge output is unparseable is tagged `unattributed` and EXCLUDED from
clustering -- never silently bucketed. A cluster is actionable only if recurrent AND addressable;
flaky one-offs are recorded with actionable:false and left alone (they are noise, not a cluster).

Usage:  python3 bin/weakness_mining_scout.py [--days 7] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))

from agentica_core.atomic import atomic_json_write  # noqa: E402
from agentica_core.evals.judge import ClassifierJudge  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_EXEC_LOG = _ROOT / "state" / "exec_log.jsonl"
_OUTPUT_LOG = _ROOT / "state" / "reflex_output.jsonl"
_STATE = _ROOT / "state" / "weakness_clusters.json"

_DEFAULT_DAYS = 7
_FAILED_STATUSES = ("error", "timeout")
# Trace bounds per failed run. A judge prompt over the char budget gaps honestly (ClassifierJudge
# refuses to front-truncate), so bound the trace here rather than discover the ceiling at runtime.
_TRACE_MAX_LINES = 40
_TRACE_MAX_CHARS = 6000
# How far back a line may sit and still belong to the run that failed. reflex_output rows carry no
# run id, so without a bound the "last N lines for this metric" would silently splice in a PREVIOUS
# run's output days earlier and attribute a mechanism the failing run never exhibited. The engine
# kills a run at EXEC_TIMEOUT_MS (default 20 min), so anything older than that + slack is a
# different run.
_TRACE_LOOKBACK_MINUTES = 25
# A mechanism must recur before it earns a fix. The paper's own rule: not every cluster implies a
# harness edit -- some reflect task difficulty, instability, or model capability limits.
_MIN_SUPPORT = 3

# Closed mechanism vocabulary. Grounded in the Self-Harness observed-failure catalogue (missing
# artifacts, deleted deliverables, unbounded exploration, exact-command retries, dependency and
# environment-persistence failures, rationalised checks) plus two mechanisms this stack actually
# exhibits: context truncation (the log-tail failure) and stale-state reads (the phantom reflex
# the remeasure_gate exists to suppress).
MECHANISMS: tuple[str, ...] = (
    "unbounded_exploration",
    "identical_retry",
    "missing_artifact",
    "tool_error_ignored",
    "context_truncation",
    "stale_state_read",
    "unverified_success",
    "quota_exhausted",
    "environment_drift",
    "other",
)

CAUSAL_STATUSES: tuple[str, ...] = ("causal", "incidental", "unknown")

# mechanism -> editable-surface keys that could plausibly move it. Empty = NOT addressable by the
# current surface. Most entries are empty on purpose: v1's surface is six numeric knobs, while the
# paper's winning edits were instructions and runtime middleware. An empty map here is the signal
# that the SURFACE needs widening -- not that the cluster is unimportant.
ADDRESSABLE: dict[str, tuple[str, ...]] = {
    "unbounded_exploration": ("loop_breaker_limit", "incomplete_limit"),
    "identical_retry": ("loop_breaker_limit",),
    "context_truncation": ("judge_max_tokens", "context_cliff_token_threshold", "scout_max_files"),
    "stale_state_read": ("reflex_cooldown_minutes",),
    "missing_artifact": (),
    "tool_error_ignored": (),
    "unverified_success": (),
    "quota_exhausted": (),      # environmental: budget/quota, not a harness knob
    "environment_drift": (),
    "other": (),
}

_MECHANISM_TEMPLATE = (
    "A governance remediation skill ran and FAILED. Classify the AGENT BEHAVIOUR that led to the "
    "failure -- not the error text itself.\n\n"
    "Skill: {input}\n"
    "Terminal outcome: {context}\n\n"
    "Execution trace (stdout tail):\n{output}\n\n"
    "Which mechanism best describes what the agent did wrong? If none fits, answer 'other'."
)

_CAUSAL_TEMPLATE = (
    "A governance remediation skill ran and FAILED.\n\n"
    "Skill: {input}\n"
    "Terminal outcome: {context}\n\n"
    "Execution trace (stdout tail):\n{output}\n\n"
    "Was the agent's own behaviour CAUSAL for this failure, or was the failure INCIDENTAL to it "
    "(an environment, quota, or infrastructure problem the agent did not cause)? Answer 'unknown' "
    "if the trace does not support a confident call."
)


def build_mechanism_judge(generate_fn=None) -> ClassifierJudge:
    """Taxonomy classifier over the closed mechanism vocabulary.

    Reuses ClassifierJudge for its tested local_guards routing, oversize refusal and honest-gap
    handling. Scores are all 0.0 with direction 'neutral': this classifies a KIND of failure, it
    does not rate quality -- `label` is the payload, `score` is meaningless here.
    """
    return ClassifierJudge(
        name="Weakness_Mechanism",
        template=_MECHANISM_TEMPLATE,
        labels=MECHANISMS,
        label_scores={m: 0.0 for m in MECHANISMS},
        direction="neutral",
        generate_fn=generate_fn,
    )


def build_causal_judge(generate_fn=None) -> ClassifierJudge:
    """Classifier for whether the agent's behaviour was causal for the terminal failure."""
    return ClassifierJudge(
        name="Weakness_Causal_Status",
        template=_CAUSAL_TEMPLATE,
        labels=CAUSAL_STATUSES,
        label_scores={s: 0.0 for s in CAUSAL_STATUSES},
        direction="neutral",
        generate_fn=generate_fn,
    )


def _parse_ts(raw) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iter_jsonl(path: Path) -> Iterable[dict]:
    """Yield parseable objects; a torn or malformed line is skipped, never fatal."""
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def load_failures(days: int = _DEFAULT_DAYS, exec_log: Optional[Path] = None) -> list[dict]:
    """Failed reflex runs inside the window, newest last.

    Only autonomous reflex runs carry a graded terminal status; manual rows are omitted by the
    exec_log contract anyway (they append only on success).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for rec in _iter_jsonl(exec_log or _EXEC_LOG):
        if rec.get("status") not in _FAILED_STATUSES:
            continue
        ts = _parse_ts(rec.get("timestamp"))
        if ts is None or ts < cutoff:
            continue
        out.append(rec)
    return out


def load_traces(days: int = _DEFAULT_DAYS, output_log: Optional[Path] = None) -> dict[str, list[tuple[datetime, str]]]:
    """metric -> [(ts, line)] from reflex_output.jsonl, oldest first.

    reflex_output rows are per-stdout-line, keyed by metric with no run id, so a run's trace is
    reconstructed by time-slicing this list. Approximate by construction -- documented rather than
    presented as an exact per-run capture.
    """
    by_metric: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)  # +1: a run precedes its row
    for rec in _iter_jsonl(output_log or _OUTPUT_LOG):
        ts = _parse_ts(rec.get("timestamp"))
        metric, line = rec.get("metric"), rec.get("line")
        if ts is None or ts < cutoff or not isinstance(metric, str) or not isinstance(line, str):
            continue
        by_metric[metric].append((ts, line))
    for lines in by_metric.values():
        lines.sort(key=lambda p: p[0])
    return by_metric


def trace_for(rec: dict, traces: dict[str, list[tuple[datetime, str]]]) -> str:
    """The stdout tail attributable to this failed run.

    Lines for the run's metric, at or before the row's timestamp and no older than the lookback
    bound. Returns "" when nothing was captured -- and an empty trace means NO attribution is
    attempted, rather than a guess from the skill name alone.
    """
    ts = _parse_ts(rec.get("timestamp"))
    metric = rec.get("reflex_id")
    if ts is None or not isinstance(metric, str):
        return ""
    floor = ts - timedelta(minutes=_TRACE_LOOKBACK_MINUTES)
    lines = [ln for (t, ln) in traces.get(metric, []) if floor <= t <= ts]
    tail = lines[-_TRACE_MAX_LINES:]
    text = "\n".join(tail)
    return text[-_TRACE_MAX_CHARS:] if len(text) > _TRACE_MAX_CHARS else text


def terminal_cause(rec: dict) -> str:
    """Verifier-level cause, derived -- not judged. The engine already made this call."""
    status = rec.get("status")
    if status == "timeout":
        return "timeout"
    detail = str(rec.get("detail") or "")
    if detail:
        low = detail.lower()
        if "quota" in low or "usage limit" in low or "rate_limit" in low:
            return "quota"
        if "not found" in low or "no such file" in low:
            return "missing_artifact"
    return "error"


def attribute(rec: dict, trace: str, mech_judge: ClassifierJudge, causal_judge: ClassifierJudge) -> Optional[dict]:
    """Failure signature for one record, or None when it cannot be attributed honestly.

    Returns None (-> `unattributed`, excluded from clustering) when there is no trace to reason
    over or when either judge gaps. An unparseable judgment is a failure, never a data point.
    """
    if not trace.strip():
        return None

    skill = str(rec.get("skill") or rec.get("command") or "unknown")
    cause = terminal_cause(rec)

    mech = mech_judge.evaluate(input=skill, output=trace, context=cause)
    if mech.is_gap or mech.label not in MECHANISMS:
        return None
    causal = causal_judge.evaluate(input=skill, output=trace, context=cause)
    if causal.is_gap or causal.label not in CAUSAL_STATUSES:
        return None

    return {
        "terminal_cause": cause,
        "causal_status": causal.label,
        "mechanism": mech.label,
        "_explanation": mech.explanation,
        "_skill": skill,
        "_reflex_id": rec.get("reflex_id"),
        "_timestamp": rec.get("timestamp"),
        "_harness_fingerprint": rec.get("harness_fingerprint"),
    }


def _signature_key(sig: dict) -> tuple[str, str, str]:
    return (sig["terminal_cause"], sig["causal_status"], sig["mechanism"])


def cluster(signatures: list[dict]) -> list[dict]:
    """Group by EXACT signature agreement, ranked by support (desc).

    Deliberately deterministic -- no embeddings, no fuzzy similarity. Two runs sharing a verifier
    outcome are kept APART when the underlying behaviour differs, because they would need
    different harness changes. Fuzzy merging would produce a cluster no single edit can fix.
    """
    buckets: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for sig in signatures:
        buckets[_signature_key(sig)].append(sig)

    clusters = []
    for (cause, causal, mech), members in buckets.items():
        keys = ADDRESSABLE.get(mech, ())
        recurrent = len(members) >= _MIN_SUPPORT
        # Incidental failures are the agent's environment, not its behaviour: no harness edit
        # addresses them, however often they recur.
        addressable = bool(keys) and causal != "incidental"
        if not recurrent:
            reason = f"support {len(members)} < {_MIN_SUPPORT}: not yet recurrent (noise, not a cluster)"
        elif not keys:
            reason = f"no editable-surface key addresses {mech!r}: the surface needs widening, not a proposal"
        elif causal == "incidental":
            reason = "agent behaviour was incidental to the failure: environmental, not a harness defect"
        else:
            reason = f"recurrent (support {len(members)}) and addressable via {', '.join(keys)}"

        clusters.append({
            "signature": {"terminal_cause": cause, "causal_status": causal, "mechanism": mech},
            "count": len(members),
            "actionable": recurrent and addressable,
            "actionability_reason": reason,
            "candidate_surface_keys": list(keys),
            "example_ids": [m.get("_reflex_id") for m in members[:3]],
            "example_skills": sorted({str(m.get("_skill")) for m in members})[:5],
            "shared_symptoms": (members[0].get("_explanation") or "")[:300],
        })
    clusters.sort(key=lambda c: (c["actionable"], c["count"]), reverse=True)
    return clusters


def run_scout(
    days: int = _DEFAULT_DAYS,
    *,
    generate_fn=None,
    exec_log: Optional[Path] = None,
    output_log: Optional[Path] = None,
    max_records: int = 60,
) -> dict:
    """Mine the window and return the payload written to state/weakness_clusters.json."""
    days = int(os.environ.get("WEAKNESS_MINING_DAYS", days))
    failures = load_failures(days, exec_log)[-max_records:]
    traces = load_traces(days, output_log)

    mech_judge = build_mechanism_judge(generate_fn=generate_fn)
    causal_judge = build_causal_judge(generate_fn=generate_fn)

    signatures, no_trace, judge_gap = [], 0, 0
    for rec in failures:
        trace = trace_for(rec, traces)
        if not trace.strip():
            no_trace += 1
            continue
        sig = attribute(rec, trace, mech_judge, causal_judge)
        if sig is None:
            judge_gap += 1
            continue
        signatures.append(sig)

    clusters = cluster(signatures)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "source": "state/exec_log.jsonl (failed runs) x state/reflex_output.jsonl (traces)",
        "records_failed": len(failures),
        "records_attributed": len(signatures),
        "records_unattributed": no_trace + judge_gap,
        # Split the gap: no_trace is a COVERAGE problem (reflex_output.jsonl holds nothing for
        # that run), judge_gap is a JUDGMENT problem (the model returned nothing usable). They
        # have different fixes, and one number hides which one is biting.
        "unattributed_no_trace": no_trace,
        "unattributed_judge_gap": judge_gap,
        "trace_coverage": round(1 - (no_trace / len(failures)), 3) if failures else None,
        "min_support": _MIN_SUPPORT,
        "clusters": clusters,
        "actionable_count": sum(1 for c in clusters if c["actionable"]),
        "top_cluster_support": max((c["count"] for c in clusters if c["actionable"]), default=0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine recurring agent failure mechanisms.")
    ap.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    ap.add_argument("--dry-run", action="store_true", help="print the payload; write nothing")
    args = ap.parse_args()

    payload = run_scout(args.days)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    atomic_json_write(_STATE, payload)
    print(
        f"wrote {_STATE}: {payload['records_failed']} failed runs, "
        f"{payload['records_attributed']} attributed, {payload['records_unattributed']} unattributed, "
        f"{len(payload['clusters'])} clusters ({payload['actionable_count']} actionable)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
