"""Governance and knowledge signals read from state files and logs: correction, rule, lesson, healing, liveness and doc-parity signals (split from aggregate.py)."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import insights, remediation, remediation_delta
from .telemetry import iso_week
from .aggregate_records import _SCOUT_CACHE_TTL_SEC, _target_week_anchor, _week_record_count


def _self_correction_rate(records: list[dict]) -> dict:
    """AUTO-005: Self-Correction Rate — the autonomic self-healing IMPROVEMENT rate:
    % of ALL autonomous-remediation attempts that actually IMPROVED their target metric
    (improved / attempted), from remediation.efficacy() over this window. A reflex/
    sensei remediation fires on a degraded metric; this measures how often the metric
    then moved the right way, from a real before/after reading per event — so it is
    calibrated from day one (no seed coefficient, unlike Est. Agent Hours Saved).

    2026-08-01 (metric-gap remediation, phase A1): sources `improvement_rate`
    (improved/attempted), not the retired `success_rate` (improved/applied). `applied`
    only counts attempts that got a judged before/after bracket — most attempts never
    do — so success_rate silently dropped the majority of no-op/unmeasured runs from
    its denominator, turning a single-digit real improvement rate into a ~52% headline.
    A window with zero attempted remediations is a data gap (nothing to rate), never a
    fabricated 0%.

    2026-08-08 (seq 9a): windowed. The counters now cover `_ACTIVE_WINDOW_DAYS` of
    exec_log instead of its whole lifetime, so an engine that has stopped firing shows
    up as a data gap ("no autonomous attempts in Nd") rather than holding its last
    lifetime rate forever. Lifetime totals remain in the efficacy dict."""
    from . import aggregate as _agg
    try:
        eff = remediation.efficacy(records=records, window_days=_agg._ACTIVE_WINDOW_DAYS)
        attempted = eff.get("attempted", 0) or 0
        rate = eff.get("improvement_rate")
        if not attempted or rate is None:
            return {"val": None, "data_gap": True, "calibrated": True,
                    "detail": eff.get("data_gap_detail") or "no judgeable remediation attempts"}
        # execution_success_rate (completed/attempted) is reported alongside the movement
        # rate so a low `rate` reads as one of two distinct problems: an engine that isn't
        # completing runs at all (low execution_success_rate — check permission/tooling
        # gates first) vs one that completes cleanly but doesn't move the metric (high
        # execution_success_rate despite low rate — check skill selection/judge timing).
        exec_rate = eff.get("execution_success_rate")
        exec_detail = (f", {eff.get('completed', 0)}/{attempted} runs completed ({exec_rate}%)"
                       if exec_rate is not None else "")
        return {"val": rate, "calibrated": True,
                "detail": (f"{eff.get('improved', 0)}/{attempted} remediation attempts improved "
                           f"their metric in the last {eff.get('window_days')}d"
                           f"{exec_detail} "
                           f"({eff.get('improved_lifetime', 0)}/{eff.get('attempted_lifetime', 0)} lifetime)")}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _mitigation_route_validity(records: list[dict]) -> dict:  # noqa: ARG001
    """Mitigation_Route_Validity (2026-08-01, metric-gap remediation, phase D1): %
    of this codebase's full metric REGISTRY (every row the dashboard emits, graded
    or not) whose mitigation route is real and not known-broken. A metric counts
    as VALID when it has both a METRIC_RULES entry (a direction to judge it by)
    and a REMEDIATION entry (a skill/command) whose resolved kind is NOT
    `mis_route` (insights.remediation_kind's explicit DO-NOT-USE marker, e.g.
    Boundary_Violations -> /guard, which can't fix existing violations). A metric
    with no rule or no route at all (an informational counter, or a compound
    AUTO-tier reducer like Self_Correction_Rate that no single skill remediates)
    is INVALID here -- it structurally has no route to validate, which is exactly
    what this metric surfaces: not every tracked number has an owner for fixing
    it. Pure computation over the static registry; no new collection, `records`
    unused. OBSERVATIONAL/informational (no METRIC_CONFIG entry, like its sibling
    Self_Correction_Rate) -- this metric describes the registry's own wiring
    health and isn't itself a thing any skill remediates."""
    from . import aggregate as _agg
    total = len(_agg.REGISTRY)
    if not total:
        return {"val": None, "data_gap": True, "calibrated": True}
    valid = 0
    for _pillar, _group, key, _fn, _tier, _is_pct, _is_cnt in _agg.REGISTRY:
        cfg = insights.METRIC_CONFIG.get(key, {})
        rem = insights.REMEDIATION.get(key)
        rule = insights.METRIC_RULES.get(key)
        if not rule or not rem:
            continue
        kind = insights.remediation_kind(
            rem["command"], readonly=cfg.get("readonly", False),
            auto_remediable=cfg.get("auto_remediable"), explicit_kind=cfg.get("kind"))
        if kind != "mis_route":
            valid += 1
    return {"val": round(100 * valid / total, 1), "calibrated": True,
            "detail": f"{valid}/{total} registry metrics have a real, non-mis-routed remediation path"}


def _remediation_delta(records: list[dict]) -> dict:  # noqa: ARG001
    """Remediation_Delta (2026-08-01, metric-gap remediation, phase B2): magnitude
    companion to Self_Correction_Rate's yes/no judgment. val = the overall median
    delta (median(3 post-firing) - median(3 pre-firing) history values, sign-
    normalized by dir) across every skill's attempts this window, from
    remediation_delta.compute(). A window with no attempts scored on both sides yet
    is a data gap (nothing to rate), never a fabricated 0. OBSERVATIONAL — see
    insights.METRIC_CONFIG["Remediation_Delta"].

    2026-08-08 (seq 9d): the detail no longer reports one lumped "N pending". Most of
    that number was never drainable — attempts missing PRE-firing history, which is
    permanent — and attempts with no METRIC_RULES entry were dropped in silence. The
    three buckets are reported separately: pending / unscoreable / unrated."""
    try:
        d = remediation_delta.compute()
        val = d.get("overall")
        counts = (f"{d.get('pending', 0)} pending, {d.get('unscoreable', 0)} unscoreable "
                  f"(no pre-firing history — permanent), {d.get('unrated', 0)} unrated "
                  f"(no metric rule)")
        if val is None:
            return {"val": None, "data_gap": True, "calibrated": True,
                    "detail": f"no attempt scored on both sides — {counts}"}
        return {"val": val, "calibrated": True,
                "detail": (f"median delta {val}% of pre-firing median across "
                           f"{len(d.get('by_skill', {}))} skill(s); {counts}")}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _verifier_falsifiability(records: list[dict]) -> dict:  # noqa: ARG001
    """Verifier_Falsifiability (2026-08-01, metric-gap remediation, phase C2): % of
    registered falsifiability checks (Order Samurai/execution/verify_falsifiability.py)
    proven to fail on a known-bad fixture AND pass on a known-clean one over this
    session's process lifetime. A verifier that never sees a deliberately-bad input
    can silently CLEAN forever -- this closes exactly that gap. `records` is ignored
    (this reads fixture/script state, not telemetry). OBSERVATIONAL: see
    insights.METRIC_CONFIG["Verifier_Falsifiability"]."""
    from . import aggregate as _agg
    try:
        if str(_agg._ORDER_SAMURAI_ROOT) not in sys.path:
            sys.path.insert(0, str(_agg._ORDER_SAMURAI_ROOT))
        from execution.verify_falsifiability import run_falsifiability
        r = run_falsifiability()
        total = r.get("total", 0) or 0
        falsifiable = r.get("falsifiable", 0)
        if not total:
            return {"val": None, "data_gap": True, "calibrated": True}
        return {"val": round(100 * falsifiable / total, 1), "calibrated": True,
                "detail": f"{falsifiable}/{total} checks proven falsifiable"}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _unbounded_wait_count(records: list[dict]) -> dict:  # noqa: ARG001
    """Unbounded_Wait_Count (2026-08-04, metric-gap remediation, phase D2): count of
    RUNTIME code sites that can block forever -- a `while`+`sleep` loop with no
    deadline, or a remote/subprocess call with no `timeout=` -- from
    Order Samurai/execution/timeout_audit_scan.py. Instruments Core Principle #8
    (every remote call gets an explicit timeout), whose only prior enforcement was
    honor-system. `records` is ignored (this reads source, not telemetry).

    Counts runtime sites only; test-fixture findings stay visible in `detail` but
    are excluded (the scanner's module docstring explains why). OBSERVATIONAL --
    see insights.METRIC_CONFIG["Unbounded_Wait_Count"].

    MEMOIZED with a short TTL (M6.2, 2026-08-16). build_pillars() runs every
    reducer, and aggregate() calls build_pillars() per view -- profiled at 22
    calls per aggregate(), each re-running this AST scan of ~200 files: 43s of a
    68s build spent recomputing one deterministic value. The scan reads source
    code, which cannot change mid-aggregate, so within-run reuse is exact; the
    TTL (not @lru_cache -- Anti-Pattern #6) bounds cross-run staleness for
    long-lived processes to a window this OBSERVATIONAL metric tolerates."""
    from . import aggregate as _agg
    now = time.monotonic()
    if _agg._unbounded_wait_memo is not None:
        stamped_at, cached = _agg._unbounded_wait_memo
        if now - stamped_at < _agg._UNBOUNDED_WAIT_TTL_S:
            return dict(cached)  # copy: envelopes downstream may be mutated per view
    try:
        if str(_agg._ORDER_SAMURAI_ROOT) not in sys.path:
            sys.path.insert(0, str(_agg._ORDER_SAMURAI_ROOT))
        from execution.timeout_audit_scan import scan_tree
        r = scan_tree(_agg._ORDER_SAMURAI_ROOT.parent)
        runtime = r.get("runtime_count", 0)
        result = {"val": runtime, "calibrated": True,
                  "detail": f"{runtime} runtime site(s) with no deadline "
                            f"({r.get('test_count', 0)} more in test fixtures, not counted)"}
        _agg._unbounded_wait_memo = (now, result)
        return dict(result)
    except Exception as e:
        # Failures are NOT memoized: a transient read error must not suppress the
        # next attempt for a whole TTL window.
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _vibe_alignment_score(records: list[dict], repo_root: Path) -> float | None:  # noqa: ARG001
    """Anti-slop vibe alignment score (0-100) from state/vibe_alignment.json.

    Written by scouts/vibe_alignment_scout.py (local gemma-4-e4b pass).
    Returns None -- a data gap, not a measured score -- when the file is absent,
    unreadable, or the last run recorded score=null/non-numeric. A genuine 0.0
    (very slopped code) and "no data" used to collapse to the same 0.0, so a
    dead scout read as a max-slop reading downstream; callers must treat None
    as a gap and must not fold it into arithmetic as if it were zero.
    """
    vibe_path = repo_root / "state" / "vibe_alignment.json"
    if not vibe_path.exists():
        return None
    try:
        d = json.loads(vibe_path.read_text(encoding="utf-8", errors="ignore"))
        score = d.get("score")
        if not isinstance(score, (int, float)):
            return None
        return float(score)
    except Exception:
        return None


def _doc_parity_latency_days(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Days between the most recently modified source file and the oldest charter doc.

    Compares the newest .py/.sh/.ts file mtime in execution/, scouts/, bin/, agentica_core/
    against the oldest .md mtime in state/charters/. A large gap means code changed
    significantly since the charters were last updated. Returns 0.0 when all docs
    are at least as fresh as the newest source change.
    """
    source_dirs = ["execution", "scouts", "bin", "agentica_core"]
    source_exts = {".py", ".sh", ".ts", ".js"}
    charter_dir = repo_root / "state" / "charters"

    # Newest source file mtime
    newest_src_mt: float = 0.0
    for sdir in source_dirs:
        d = repo_root / sdir
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix in source_exts and p.is_file():
                try:
                    newest_src_mt = max(newest_src_mt, p.stat().st_mtime)
                except OSError:
                    pass

    # Oldest charter doc mtime
    oldest_doc_mt: float = float("inf")
    if charter_dir.exists():
        for p in charter_dir.glob("*.md"):
            try:
                oldest_doc_mt = min(oldest_doc_mt, p.stat().st_mtime)
            except OSError:
                pass

    if newest_src_mt == 0.0 or oldest_doc_mt == float("inf"):
        return 0.0

    gap_seconds = max(0.0, newest_src_mt - oldest_doc_mt)
    return round(gap_seconds / 86400, 1)  # days


def _mechanism_liveness(records: list[dict]) -> dict:
    """AUTO-019: Mechanism Liveness — real count of mechanism_run events (registered
    mechanisms that ran AND had their output consumed, the 3-step Mechanism Rule) in
    the CANONICAL cross-platform stream (Data/telemetry/autonomic_events.jsonl).

    Two real producers feed this stream, no fabrication in either:
      - scouts/autonomic_events_scout.py (bow pillar): bridges the mechanism_audit
        health-check's real ~/.claude/data/mechanism_audit.json output — a mechanism_run
        is only emitted when that file exists with a genuine generated_at + counts shape.
      - Governance/refresh_dashboard.py `_emit_mechanism_runs()`: bridges real
        ReflexEngine autonomous runs recorded in Order Samurai's state/exec_log.jsonl.

    A window with zero mechanism_run events anywhere in the stream is a genuine data
    gap (nothing observed yet), never a fabricated zero — mirrors _kill_chains_open's
    honesty rule for a dead/unwired emitter.

    2026-08-08 class sweep: `records` is not this metric's data source but it does say
    WHICH week is being asked about (see _target_week_anchor), so a backfill replay of
    week N counts week N's mechanism_run events rather than the current week's."""
    from . import aggregate as _agg
    path = _agg.default_events_path()
    if not path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}
    now = _target_week_anchor(records)
    this_week = now.strftime("%G-W%V")
    last_week = (now - timedelta(days=7)).strftime("%G-W%V")
    this_count = last_count = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("event") != "mechanism_run":
                continue
            wk = iso_week(rec.get("timestamp"))
            if wk == this_week:
                this_count += 1
            elif wk == last_week:
                last_count += 1
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}
    starved = _starved_heartbeats(this_week) if this_count == 0 else 0
    # == 0, not falsy: an unreadable or malformed exec_log (None) is missing evidence,
    # not "no runs".
    if starved and _week_record_count(_agg._REFLEX_EXEC_LOG, this_week, strict=True) == 0:
        # Idle by design (owner decision 2026-10-07, option B): after F4 a cycle with
        # nothing eligible writes no exec_log row, so no mechanism_run is emitted.
        # Heartbeats prove the loop ran, so report that state ungraded instead of a
        # FAIL. No heartbeat, or an engine run that never got bridged, keeps the 0.
        return {"val": None, "state": "idle_by_design", "data_gap": True,
                "calibrated": True,
                "detail": (f"idle by design: 0 mechanism runs, {starved} starved "
                           f"heartbeat(s) in {this_week} (reflex loop alive, nothing "
                           f"eligible to remediate)")}
    if this_count == 0 and last_count == 0:
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    return {"val": this_count, "week_delta": this_count - last_count, "calibrated": True}


def recorded_idle_still_valid(week: str) -> bool:
    """Whether a week history recorded as Mechanism_Liveness idle_by_design may stay
    idle on rebuild. The heartbeats that proved idle rotate away, but any engine run
    logged that week (or an unreadable or malformed exec_log) means the 0 is a real FAIL."""
    from . import aggregate as _agg
    return _week_record_count(_agg._REFLEX_EXEC_LOG, week, strict=True) == 0


def _starved_heartbeats(week: str) -> int:
    """Count the reflex loop's "[starved]" heartbeats in ISO `week` (0 if unreadable)."""
    from . import aggregate as _agg
    return _week_record_count(
        _agg._REFLEX_HEARTBEAT_LOG, week,
        lambda r: r.get("metric") == "reflex" and str(r.get("line", "")).startswith("[starved]")) or 0


def _lesson_graduation_rate(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-017 (Lesson Graduation Rate): fraction of skills ever added to the real
    "lesson ledger" (`~/.claude/data/skill_improve_queue.jsonl` — reflex_eureka's
    per-invocation queue, one entry per skill use awaiting a 24h post-hoc
    effectiveness review) that have GRADUATED into a durable, reusable RULE — a
    skill classified >= 70% autonomous-improvement effective across >= 5 runs in
    the "RULE — High-Effectiveness Skills" section of `~/.claude/data/
    auto_eureka_skills.md` (the report reflex_eureka.py regenerates from
    exec_log.jsonl).

    A skill "graduates" once its own real usage history earns reflex_eureka's
    trust as a proven autonomous remediator; everything else still sitting in the
    ledger (never classified, or classified GOTCHA/CONTEXT/insufficient-data) has
    NOT graduated yet.

    Ignores `records` — this metric's real source lives outside the canonical
    telemetry schema. Returns a data_gap envelope (never a fabricated rate) when
    the ledger is missing or has never captured a lesson."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    data_dir = home / ".claude" / "data"
    queue_path = data_dir / "skill_improve_queue.jsonl"
    eureka_path = data_dir / "auto_eureka_skills.md"

    if not queue_path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}

    queued_skills: set[str] = set()
    try:
        for line in queue_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            skill = rec.get("skill") if isinstance(rec, dict) else None
            if isinstance(skill, str) and skill.strip():
                queued_skills.add(skill.strip().lower())
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}

    if not queued_skills:
        # Ledger file exists but has never captured a single lesson — genuine
        # data gap, not a fabricated 0%.
        return {"val": None, "data_gap": True, "calibrated": True}

    graduated_skills: set[str] = set()
    if eureka_path.exists():
        try:
            md = eureka_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            md = ""
        section_match = re.search(
            r"## RULE — High-Effectiveness Skills(.*?)(?:\n## |\Z)", md, re.DOTALL)
        section = section_match.group(1) if section_match else ""
        for m in re.finditer(r"\*\*`/?([\w-]+)`\*\*", section):
            graduated_skills.add(m.group(1).strip().lower())

    graduated_in_ledger = queued_skills & graduated_skills
    rate = round(100 * len(graduated_in_ledger) / len(queued_skills), 1)
    return {
        "val": rate,
        "calibrated": True,
        "detail": f"{len(graduated_in_ledger)}/{len(queued_skills)} ledger skills graduated to RULE",
    }


def _dead_rule_count(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-014 (Dead-Rule Detection): how many governance anti-pattern rules have NOT
    fired within the retirement window — cruft rules that scan every session but never
    catch anything. A direct Brush architecture-hygiene signal (dead rules are dead
    weight to retire).

    Reads the canonical rule set and the real firing log straight from the config
    tier's principle_audit.py (~/.claude/scripts): its PATTERNS list is the full
    denominator and ~/.claude/data/principle_violations.jsonl records every hit with a
    `ts`. A rule absent from the last RETIREMENT_WINDOW_DAYS of hits is "dead" — exactly
    the retirement-candidate set `principle_audit.py --retirement` prints. This mirrors
    cmd_retirement byte-for-byte (same cutoff, same `ts`>=cutoff test, same skip-on-
    error) so the metric can never disagree with the canonical tool. Importing the
    module for PATTERNS (rather than hardcoding the rule ids) means the count tracks the
    audit's own rule set and can never silently drift when a rule is added or removed.

    Ignores `records` (the telemetry stream carries no rule-firing data) and reads the
    source directly with a short TTL cache. Returns a data_gap envelope — never a
    fabricated 0 — when the config tier, its rule set, or its violation log is
    unreadable, so an absent source grades SIMULATED instead of faking "0 dead rules"."""
    from . import aggregate as _agg
    now = time.monotonic()
    if (_agg._DEAD_RULE_CACHE["v"] is not None
            and now - _agg._DEAD_RULE_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _agg._DEAD_RULE_CACHE["v"]

    def _gap() -> dict:
        from . import aggregate as _agg
        r = {"val": None, "data_gap": True, "calibrated": True}
        _agg._DEAD_RULE_CACHE.update(t=now, v=r)
        return r

    scripts_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "scripts"
    if not scripts_dir.exists():
        return _gap()
    import sys
    # Put scripts_dir on sys.path only for this import, then take it back off.
    # Leaving ~/.claude/scripts (and its sibling ~/.claude) behind permanently
    # shadows other imports for the rest of the process: ~/.claude/execution is a
    # REGULAR package, so it beats a platform's namespace-portion `execution` even
    # when that platform's root is inserted at sys.path[0] -- which broke
    # load_verifiers("claude") with a bogus ModuleNotFoundError.
    saved_path = list(sys.path)
    sys.path.insert(0, str(scripts_dir))
    try:
        import principle_audit as pa  # canonical rule set + violation log paths
    except Exception:
        return _gap()
    finally:
        sys.path[:] = saved_path
    patterns = getattr(pa, "PATTERNS", None)
    violations_file = getattr(pa, "VIOLATIONS_FILE", None)
    window_days = getattr(pa, "RETIREMENT_WINDOW_DAYS", 90)
    if not patterns or violations_file is None:
        return _gap()
    rule_ids = [p.get("rule_id") for p in patterns if isinstance(p, dict) and p.get("rule_id")]
    if not rule_ids:
        return _gap()
    if not Path(violations_file).exists():
        return _gap()
    # cmd_retirement canonicalizes each hit's rule_id before adding it to `seen`
    # (a rule rename maps its old id forward, e.g. LEGACY_RULE_IDS); without the
    # same step here, a hit logged under a pre-rename id never matches the
    # PATTERNS list's current id and the live rule reads as dead with 0 hits.
    _canon = getattr(pa, "_canonical_rule_id", lambda rid: rid)
    cutoff = datetime.now() - timedelta(days=window_days)
    seen: set[str] = set()
    try:
        with open(violations_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    v = json.loads(line)
                    if datetime.fromisoformat(v["ts"]) >= cutoff:
                        seen.add(_canon(v["rule_id"]))
                except Exception:
                    continue
    except OSError:
        return _gap()
    dead = sorted(rid for rid in rule_ids if rid not in seen)
    result = {"val": len(dead), "calibrated": True,
              "detail": f"{len(dead)}/{len(rule_ids)} rules dead ({window_days}d, 0 hits): "
                        + (", ".join(dead) or "none")}
    _agg._DEAD_RULE_CACHE.update(t=now, v=result)
    return result


def _mean_time_to_heal(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-018 (Mean Time to Heal): mean elapsed seconds from a reflex's FIRST
    autonomous remediation fire to the FIRST fire that actually healed its target
    (improved=true), read directly from Order Samurai's state/exec_log.jsonl.

    This measures the REMEDIATION-effort span, NOT detection-to-heal latency: exec_log
    records the moment a remediation ran, not the moment the metric first went bad (no
    such detection timestamp is logged anywhere on this host), so the clock starts at
    the first remediation attempt for a reflex_id, not at the degradation itself. The
    metric is named for what it truly measures.

    Per reflex_id: sort its events by timestamp, take the first event's time as the
    start and the first event with improved==True as the heal; the span is their
    difference in seconds (0 when a reflex heals on its first attempt). Reflexes that
    never reached improved==True are STILL OPEN and are EXCLUDED from the mean (never
    counted as 0 — that would fabricate a heal that never happened). If no reflex_id
    has ever healed, returns a data_gap envelope, never a fabricated duration.

    Mirrors _self_correction_rate / _cache_hit_rate: it ignores the `records` arg
    (which carries no per-event reflex_id/improved timing) and reads the source file
    directly with a short TTL cache so repeated aggregate() calls don't re-scan."""
    from . import aggregate as _agg
    now = time.monotonic()
    if (_agg._MTTH_CACHE["v"] is not None
            and now - _agg._MTTH_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _agg._MTTH_CACHE["v"]
    exec_log = _agg._ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl"
    if not exec_log.exists():
        result = {"val": None, "data_gap": True, "calibrated": True}
        _agg._MTTH_CACHE.update(t=now, v=result)
        return result
    by_reflex: dict[str, list[tuple[datetime, bool]]] = defaultdict(list)
    try:
        with open(exec_log, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"reflex_id"' not in line:  # cheap pre-filter before json.loads
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                rid = entry.get("reflex_id")
                ts = entry.get("timestamp")
                if not rid or not ts or "improved" not in entry:
                    continue
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                by_reflex[rid].append((dt, entry.get("improved") is True))
    except OSError:
        result = {"val": None, "data_gap": True, "calibrated": True}
        _agg._MTTH_CACHE.update(t=now, v=result)
        return result
    spans: list[float] = []
    for events in by_reflex.values():
        events.sort(key=lambda e: e[0])
        start = events[0][0]
        heal = next((dt for dt, improved in events if improved), None)
        if heal is not None:
            spans.append(max(0.0, (heal - start).total_seconds()))
    if not spans:
        # No reflex has ever reached improved=true — nothing has healed yet, so there
        # is no real duration to report (never a fabricated 0).
        result = {"val": None, "data_gap": True, "calibrated": True}
    else:
        result = {"val": round(sum(spans) / len(spans)), "calibrated": True,
                  "detail": f"{len(spans)} reflex(es) healed; mean first-fire→first-heal span (s)"}
    _agg._MTTH_CACHE.update(t=now, v=result)
    return result
