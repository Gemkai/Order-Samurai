"""Compute per-skill efficacy from exec_log.jsonl and write skill_efficacy.json.

Called non-fatally by refresh_dashboard.py on every refresh. Feeds the dynamic
cooldown multiplier in ReflexEngine: skills that consistently fail get longer
cooldowns, reducing runaway retry noise and surfacing systemic skill issues.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .telemetry import parse_ts

_WINDOW = 20            # consider only the last N runs per skill
_WARMUP_RUNS = 3        # fewer than this → not enough data, retry aggressively
_WARMUP_MULTIPLIER = 0.25   # 0.25× COOLDOWN_MS = 7.5 min (optimistic until proven)
_LOW_THRESHOLD = 0.30   # below 30% success rate → apply penalty multiplier
_MULTIPLIER = 3         # 3× normal COOLDOWN_MS (30 min → 90 min)
# Graded runs older than this stop steering the multiplier. 14, not 30 (user-ratified
# 2026-08-08): aging is deliberately SYMMETRIC — it drops stale successes as well as stale
# failures, because dropping only failures would be grading inflation, and inflated efficacy is
# what hid the 18-day remediation outage. The cost of that symmetry is that a skill whose
# successes are older than its failures gets throttled HARDER: at 30d, simplify went 7/20 (1x)
# to 0/11 (3x) and model-selector 1/14 to 0/9. Their recent record genuinely is poor, so some
# throttling is right — 3x on an 11-run sample is just heavier than the evidence carries. A
# shorter window keeps the symmetry and lets a skill re-earn its multiplier sooner.
_AGING_DAYS = 14

# Late re-judgment sidecar, written by refresh_dashboard._rejudge_late_improvements().
# NEVER a second copy of exec_log.jsonl — exec_log is hash-chained and must stay
# byte-stable, so a verdict that only becomes knowable later lands here instead.
_SIDECAR_NAME = "exec_log_rejudge.jsonl"


def _kill_switch_off(name: str) -> bool:
    """True when env var `name` explicitly disables a default-ON behaviour. Same
    house idiom as remediation._kill_switch_off / doctor.AUDIT_CANARY_ENABLED: the
    switch defaults to the FIXED behaviour, so an operator can only opt BACK to the
    old semantics."""
    return os.environ.get(name, "true").strip().lower() in ("false", "0", "no")


def load_rejudged_hashes(path: Path) -> set[str]:
    """`entry_hash` values the late-re-judgment sidecar has upgraded to improved.

    Why a sidecar at all: the engine decides `improved` exactly once, at fire time —
    the reflex id must vanish from wid_payload within the single synchronous ≤60 s
    post-run refresh (reflex-engine.ts::computeImproved). A remediation whose metric
    only recomputes on a nightly/weekly cadence (vault scans, staleness-days,
    doc-parity) is therefore recorded improved:false forever, which lands here as a
    permanent 0% success rate and a 3× cooldown on a skill that actually worked.
    The sidecar carries the later verdict WITHOUT touching the tamper-evident log.

    Only rows whose own `improved` is True count — the file is append-only and
    keyed by entry_hash, so a future verdict kind cannot silently grade a run.

    Kill switch: REJUDGE_SIDECAR_ENABLED=false ignores the file entirely (restores
    the fire-time-only verdict). Shared with remediation.efficacy(), which applies
    the same upgrade to its tier-2 engine-verdict events.
    """
    if _kill_switch_off("REJUDGE_SIDECAR_ENABLED"):
        return set()
    out: set[str] = set()
    path = Path(path)
    if not path.exists():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("improved") is True:
            h = row.get("entry_hash")
            if isinstance(h, str) and h:
                out.add(h)
    return out


def compute(log_path: Path, out_path: Path, rejudge_path: Path | None = None,
            now: datetime | None = None) -> dict:
    """Parse exec_log.jsonl and write skill_efficacy.json.

    Returns the efficacy dict (skill → {total_runs, success_count, success_rate,
    cooldown_multiplier}).  Reads up to _WINDOW runs per skill from the tail of
    the log (newest-first traversal) so recent failures weigh more than old ones.

    Success for LLM-skill rows is the explicit ``improved`` boolean ONLY (written
    by ReflexEngine after comparing pre/post metric state — real metric movement).
    Rows lacking the field are SKIPPED, not counted: the old ``status == "done"``
    exit-code fallback graded legacy rows on "the process exited 0", inflating
    wiki to 10/20 and codebase-cleanup-deps-audit to 10/15 while genuine
    improvements across the whole log were 11/180 — and this file feeds the
    cooldown multiplier AND the maturity/grant ladder, so the proxy was steering
    autonomy decisions (2026-07-26 audit W3). An ungraded run is unknown, not a
    success and not a failure. Read-only mechanisms keep exit-0 grading under
    their separate ``<skill>::mechanism`` key — for a detect script, a clean run
    IS its success.

    LATE RE-JUDGMENT (2026-08-08): a fire-time ``improved: false`` is upgraded to a
    success when the run's ``entry_hash`` appears in the sidecar written by
    refresh_dashboard (``state/exec_log_rejudge.jsonl``, see load_rejudged_hashes).
    `rejudge_path` defaults to that file next to `log_path`.

    AGING (2026-08-08): graded runs older than ``_AGING_DAYS`` are DISCARDED
    before the window cap, so a skill whose failures are all stale falls back to
    warmup (0.25×) instead of staying parked at the 3× penalty. Aging only ever
    REMOVES rows — it never grades one, so the W3 discipline is untouched: a run
    whose success was never measured (no explicit ``improved``) is still skipped and
    can never become a success by getting old. A skill whose ONLY graded rows aged
    out is emitted with ``total_runs: 0`` plus an ``aged_out`` count — deliberately
    distinct from the never-graded case, which still produces no record at all.
    `now` is injectable so tests never assert against a live clock.

    Kill switches (both default ON, ratified 2026-08-08): REJUDGE_SIDECAR_ENABLED
    and SKILL_EFFICACY_AGING_ENABLED. With both off this function reproduces the
    pre-2026-08-08 numbers exactly.
    """
    log_path = Path(log_path)
    rejudged = load_rejudged_hashes(
        rejudge_path if rejudge_path is not None else log_path.parent / _SIDECAR_NAME
    )
    aging_on = not _kill_switch_off("SKILL_EFFICACY_AGING_ENABLED")
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    cutoff = ref - timedelta(days=_AGING_DAYS)

    def _is_stale(row: dict) -> bool:
        """True only when the row carries a readable timestamp AND it predates the
        aging cutoff. An undated row counts as fresh: dropping rows we cannot date
        would silently discard real evidence."""
        if not aging_on:
            return False
        dt = parse_ts(row.get("timestamp"))
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < cutoff

    runs: dict[str, list[bool]] = defaultdict(list)  # skill → list of booleans (True=improved)
    aged_out: dict[str, int] = defaultdict(int)      # skill → graded rows dropped by aging
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for ln in reversed(lines):  # newest first
            try:
                r = json.loads(ln)
                # Use "skill" field if present; fall back to the bare skill name from
                # the command string — same normalization as reflex_eureka, so both
                # modules (and ReflexEngine's efficacy lookup) bucket runs identically.
                skill = r.get("skill")
                if not skill:
                    parts = (r.get("command") or "unknown").strip().lstrip("/").split()
                    skill = parts[0] if parts else "unknown"
                # Read-only mechanisms (detect scripts) never move their own metric, so
                # 'improved' is always false. Grade them by a clean run (exit-0) under a
                # separate "<skill>::mechanism" key, so the mechanism's honest record isn't
                # blended with the retired LLM skill's improved-based failures (the maturity
                # ladder reads this file; blending demotes a working mechanism to OBSERVE).
                is_ro_mech = r.get("kind") == "mechanism" and r.get("read_only") is True
                key = f"{skill}::mechanism" if is_ro_mech else skill
                if is_ro_mech:
                    graded, verdict = True, r.get("status") == "done"
                else:
                    # Explicit improved verdict only — ungraded legacy rows
                    # are skipped, never proxied from the exit code.
                    improved = r.get("improved")
                    graded = improved is not None
                    verdict = bool(improved)
                    # Late re-judgment: the metric moved after the engine's ≤60 s
                    # judgment window, so the sidecar carries the real verdict.
                    if graded and not verdict:
                        h = r.get("entry_hash")
                        if isinstance(h, str) and h in rejudged:
                            verdict = True
                if not graded:
                    continue
                # Aging runs BEFORE the window cap so stale rows never occupy a slot
                # a fresh run could have used.
                if _is_stale(r):
                    aged_out[key] += 1
                    continue
                if len(runs[key]) < _WINDOW:
                    runs[key].append(verdict)
            except (json.JSONDecodeError, TypeError):
                pass

    efficacy: dict[str, dict] = {}
    for skill in list(runs) + [k for k in aged_out if k not in runs]:
        successes_list = runs.get(skill) or []
        stale_count = aged_out.get(skill, 0)
        if not successes_list and not stale_count:
            continue  # only ungraded rows seen — no record, not a fake 0-run entry
        total = len(successes_list)
        successes = sum(1 for x in successes_list if x)
        rate = round(successes / total, 3) if total else None
        if total < _WARMUP_RUNS:
            # Insufficient history — retry aggressively until we have signal
            multiplier = _WARMUP_MULTIPLIER
        elif rate is not None and rate < _LOW_THRESHOLD:
            # Proven consistently failing — back off hard
            multiplier = _MULTIPLIER
        else:
            multiplier = 1

        efficacy[skill] = {
            "total_runs": total,
            "success_count": successes,
            "success_rate": rate,
            "cooldown_multiplier": multiplier,
        }
        if stale_count:
            # Only present when aging actually dropped something, so an unaged log
            # (or the kill switch) produces byte-identical output to before.
            efficacy[skill]["aged_out"] = stale_count

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(efficacy, indent=2), encoding="utf-8")
    return efficacy
