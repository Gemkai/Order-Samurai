from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verify_archive_boundaries import run_checks as run_archive_boundary_checks
from execution.verify_archive_boundaries import summarize as summarize_archive_boundary_checks
from execution.verify_path_authority import run_checks as run_path_authority_checks
from execution.verify_path_authority import summarize as summarize_path_authority_checks
from execution.verify_root_hygiene import run_checks as run_root_hygiene_checks
from execution.verify_root_hygiene import summarize as summarize_root_hygiene_checks
from execution.verify_agentica_root_hygiene import run_checks as run_agentica_root_hygiene_checks
from execution.verify_agentica_root_hygiene import summarize as summarize_agentica_root_hygiene_checks
from execution.verify_no_stale_paths import run_checks as run_stale_path_checks
from execution.verify_no_stale_paths import summarize as summarize_stale_path_checks
from execution.verify_live_sources import run_checks as run_live_source_checks
from execution.verify_live_sources import summarize as summarize_live_source_checks
from execution.verify_runtime_contract import run_checks as run_runtime_contract_checks
from execution.verify_runtime_contract import summarize as summarize_runtime_contract_checks
from execution.verify_telemetry_freshness import run_checks as run_telemetry_freshness_checks
from execution.verify_telemetry_freshness import summarize as summarize_telemetry_freshness_checks
from execution.score_claude_architecture import run_checks as run_claude_arch_checks
from execution.score_claude_architecture import summarize as summarize_claude_arch_checks


def _run_meditation_timestamp_checks() -> list[dict]:
    """WARN on done/doing backlog items missing their calibration timestamps.

    Calibration coefficients only accumulate from (started_at, completed_at)
    pairs — an unstamped done item is a silently lost sample.
    Fix: python bin/stamp_meditation_timestamps.py
    """
    import json
    import subprocess
    state = ROOT_DIR / "state" / "MEDITATION_STATE.json"
    if not state.exists():
        return []
    # Pre-doctor backstop: the stamp script is the CODE chokepoint for the
    # at-dispatch/at-transition capture the meditation prompt (Step C/F) is
    # merely instructed to do. Running it here means every doctor invocation
    # (Step A-prime, Step E, sensei-cycle, manual) closes the leak window, so
    # the WARNs below only report what code could not recover. Idempotent; a
    # backstop failure must never break the health check itself.
    stamp = ROOT_DIR / "bin" / "stamp_meditation_timestamps.py"
    if stamp.exists():
        try:
            subprocess.run([sys.executable, str(stamp)], capture_output=True,
                           timeout=30, check=False)
        except Exception:
            pass
    try:
        backlog = json.loads(state.read_text(encoding="utf-8")).get("backlog", [])
    except Exception as exc:
        return [{"status": "WARN", "label": "meditation-timestamps",
                 "detail": f"MEDITATION_STATE.json unreadable: {exc}"}]
    # Recoverable: a backstop run of stamp_meditation_timestamps.py can fill these.
    recoverable = [i.get("id", "?") for i in backlog
                   if (i.get("status") == "done" and not i.get("completed_at"))
                   or (i.get("status") == "doing" and not i.get("started_at"))]
    # Lost: a done item with no started_at has no honest source for it (commit-span
    # is not work-duration; stamping it = a fabricated 0-min sample). The real fix is
    # forward — stamp started_at at dispatch, not only at cycle end. IDs in the
    # explicit baseline file are acknowledged-lost (pre-chokepoint history): excluded
    # here so this WARN only fires on NEW leaks, i.e. the transition-backstop in
    # stamp_meditation_timestamps.py failed to observe a transition.
    baseline_file = ROOT_DIR / "state" / "calibration_lost_baseline.json"
    baselined_ids: set = set()
    try:
        baselined_ids = set(json.loads(baseline_file.read_text(encoding="utf-8"))
                            .get("baselined", []))
    except Exception:
        pass
    all_lost = [i.get("id", "?") for i in backlog
                if i.get("status") == "done" and not i.get("started_at")]
    lost = [i for i in all_lost if i not in baselined_ids]
    baselined_seen = len(all_lost) - len(lost)
    results: list[dict] = []
    if recoverable:
        results.append({"status": "WARN", "label": "meditation-timestamps",
                        "detail": f"{len(recoverable)} item(s) missing recoverable timestamps "
                                  f"({', '.join(recoverable[:5])}) — run bin/stamp_meditation_timestamps.py"})
    if lost:
        results.append({"status": "WARN", "label": "meditation-timestamps.lost-samples",
                        "detail": f"{len(lost)} done item(s) missing started_at with no recoverable source "
                                  f"({', '.join(lost[:5])}) — calibration samples permanently lost; fix "
                                  f"forward capture (stamp started_at at dispatch, not only at cycle end)"})
    if baselined_seen:
        results.append({"status": "OK", "label": "meditation-timestamps.lost-samples",
                        "detail": f"{baselined_seen} acknowledged-lost item(s) excluded via "
                                  f"state/calibration_lost_baseline.json"})
    if not results:
        results.append({"status": "OK", "label": "meditation-timestamps",
                        "detail": "all done/doing backlog items carry calibration timestamps"})
    return results


def _run_schema_violation_checks(state_dir: Path | None = None, now=None) -> list[dict]:
    """Report how many consecutive clean days the Phase A2 warn-only schema sink has.

    A3 flips `sensei_writeback` from warn-only to enforce after 7 clean days. That
    gate needs something that actually counts the days — otherwise "7 clean days"
    is a claim nobody measured. This is the observer, and it lives here rather than
    in its own scheduled job because doctor already runs on the meditation cadence
    and already reads state/ (Mechanism budget: no new launchd entry).

    WARN-only on purpose. A schema violation is the signal A3 is waiting FOR; a
    gate that FAILs doctor on one would halt the overnight cycle over exactly the
    observation it exists to collect.

    The counter resets from the newest violation, not from the stamp: a stamp that
    outlived a violation would report a clean streak that never happened.
    """
    import json
    from datetime import datetime, timezone

    state_dir = state_dir or (ROOT_DIR / "state")
    stamp_path = state_dir / "schema_violations_clean_since.json"
    sink_path = state_dir / "schema_violations.jsonl"
    label = "schema-violations-clean"

    if not stamp_path.exists():
        return [{"status": "WARN", "label": label,
                 "detail": f"no clean-since stamp at {stamp_path.name} — A3's 7-day gate "
                           f"has no start date to count from"}]
    try:
        since = datetime.fromisoformat(
            json.loads(stamp_path.read_text(encoding="utf-8"))["clean_since"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"status": "WARN", "label": label,
                 "detail": f"{stamp_path.name} unreadable or missing clean_since: {exc}"}]

    # Newest violation timestamp in the sink, if the sink exists at all. Absent
    # sink == zero violations: check_warn_only creates it lazily on the first one.
    newest = None
    violations = 0
    if sink_path.exists():
        try:
            for line in sink_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                except (ValueError, TypeError, KeyError):
                    continue
                violations += 1
                if newest is None or ts > newest:
                    newest = ts
        except OSError as exc:
            return [{"status": "WARN", "label": label,
                     "detail": f"{sink_path.name} unreadable: {exc}"}]

    # A violation newer than the stamp restarts the count from that violation.
    reset_by = newest if newest and newest > since else None
    now = now or datetime.now(timezone.utc)
    days = (now - (reset_by or since)).total_seconds() / 86400.0

    if reset_by:
        return [{"status": "WARN", "label": label,
                 "detail": f"{violations} violation(s) recorded; newest at "
                           f"{reset_by.isoformat()} reset the streak to {days:.1f}d — "
                           f"A3 cannot flip until it reaches 7d"}]
    return [{"status": "OK", "label": label,
             "detail": f"{days:.1f} clean day(s) since {since.date()} "
                       f"({'A3 flip-eligible' if days >= 7 else 'A3 gate: 7d'})"}]


def _run_local_llm_checks() -> list[dict]:
    """WARN when the local LLM (Ollama) endpoint is unreachable.

    The model router (agentica_core.model_router) and bin/ronin-local route
    classification/bulk work to a local Ollama server, falling back to paid
    cloud APIs on failure. That fallback is SILENT: a dead local tier surfaces
    only as higher cost and a collapsing Local_Routing_Share (which reads None,
    not 0, when there are no local records) -- never as an error. This probe
    converts that silent outage into a visible WARN the daemon-health gate
    catches. Fix: start Ollama ('ollama serve') or the Ollama desktop app.
    """
    import json
    import os
    import urllib.request

    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            models = [m.get("name") for m in json.loads(resp.read()).get("models", [])]
    except Exception as exc:
        return [{"status": "WARN", "label": "local-llm",
                 "detail": f"Ollama unreachable at {base} ({exc.__class__.__name__}); local "
                           f"routing is silently falling back to paid cloud APIs -- start "
                           f"Ollama ('ollama serve') or the desktop app"}]
    if not models:
        return [{"status": "WARN", "label": "local-llm",
                 "detail": f"Ollama reachable at {base} but no models pulled -- local routing "
                           f"will fall back to cloud (run 'ollama pull gemma4:4b')"}]
    return [{"status": "OK", "label": "local-llm",
             "detail": f"Ollama reachable at {base} ({len(models)} model(s): "
                       f"{', '.join(m for m in models[:3] if m)})"}]


def _run_claude_telemetry_checks(max_age_hours: float = 48.0,
                                 source: "Path | None" = None,
                                 now=None) -> list[dict]:
    """FAIL (not WARN) when the newest Claude telemetry record exceeds max_age_hours.

    The SessionEnd emitter swallows every error by design ("never break a
    session over telemetry"), so a dead emitter leaves no trace anywhere — the
    2026-06-21 → 2026-07-06 outage ran 15 days undetected while dashboards
    quietly served stale metrics. Record recency is the only liveness signal
    the pipeline has, so staleness must gate (doctor exit 1), not annotate.

    `source`/`now` are injectable for tests; at runtime the sink path comes
    from the kernel's platform registry — never hardcode it here.
    """
    from datetime import datetime, timezone

    governance = ROOT_DIR.parent
    if str(governance) not in sys.path:
        sys.path.insert(0, str(governance))
    try:
        from agentica_core.adapter import resolve_platform
        from agentica_core.telemetry import parse_ts
    except Exception as exc:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"kernel import failed ({exc}) — cannot resolve telemetry source"}]
    if source is None:
        try:
            source = resolve_platform("claude").telemetry_source
        except Exception as exc:
            return [{"status": "FAIL", "label": "claude-telemetry",
                     "detail": f"platform registry unresolvable ({exc})"}]
    if not source.exists():
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"no telemetry file at {source} — the SessionEnd emitter "
                           f"has never landed a record on this machine"}]

    import json
    newest = None
    try:
        # errors="ignore" matches verify_telemetry_freshness: a truncated multibyte
        # tail from a killed write must not false-FAIL the gate as "unreadable".
        with source.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    dt = parse_ts(json.loads(line).get("timestamp"))
                except Exception:
                    continue
                if dt is not None and (newest is None or dt > newest):
                    newest = dt
    except Exception as exc:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"telemetry file unreadable: {exc}"}]
    if newest is None:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"{source} contains no parseable timestamped records"}]

    now = now or datetime.now(timezone.utc)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    age_h = (now - newest).total_seconds() / 3600.0
    if age_h > max_age_hours:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"newest record is {age_h:.1f}h old (limit {max_age_hours:.0f}h) — "
                           f"the emitter is silently dead; check the SessionEnd hook and "
                           f"~/.claude/scripts/agentica_emit.py"}]
    return [{"status": "OK", "label": "claude-telemetry",
             "detail": f"newest record {age_h:.1f}h old (limit {max_age_hours:.0f}h)"}]


def main() -> int:
    print("Order Samurai Doctor")
    print("--------------------")

    path_results = run_path_authority_checks()
    for result in path_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    stale_results = run_stale_path_checks()
    for result in stale_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    live_source_results = run_live_source_checks()
    for result in live_source_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    runtime_results = run_runtime_contract_checks()
    for result in runtime_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    root_results = run_root_hygiene_checks()
    for result in root_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    agentica_root_results = run_agentica_root_hygiene_checks()
    for result in agentica_root_results:
        print(f"[{result['status']}] {result['name']}: {result['detail']}")

    archive_results = run_archive_boundary_checks()
    for result in archive_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    meditation_ts_results = _run_meditation_timestamp_checks()
    for result in meditation_ts_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    schema_clean_results = _run_schema_violation_checks()
    for result in schema_clean_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    local_llm_results = _run_local_llm_checks()
    for result in local_llm_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    # GATE, not spectator: a silent SessionEnd-emitter death ran 15 days undetected
    # in June 2026. A stale stream FAILs doctor (affects exit code), never just WARNs.
    telemetry_results = run_telemetry_freshness_checks()
    for result in telemetry_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    claude_tel_results = _run_claude_telemetry_checks()
    for result in claude_tel_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    # Claude architecture score — the enforcement pack's live verdict on the
    # ~/.claude runtime, folded in as a GATE (a zeroed category FAILs doctor).
    # This is the "100/100 becomes continuously enforced, not a one-time
    # judgment" contract from the claude verifier backlog's Definition of Done.
    claude_arch_results = run_claude_arch_checks()
    for result in claude_arch_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    path_counts, path_exit = summarize_path_authority_checks(path_results)
    stale_counts, stale_exit = summarize_stale_path_checks(stale_results)
    live_source_counts, live_source_exit = summarize_live_source_checks(live_source_results)
    runtime_counts, runtime_exit = summarize_runtime_contract_checks(runtime_results)
    root_counts, root_exit = summarize_root_hygiene_checks(root_results)
    agentica_root_counts, agentica_root_exit = summarize_agentica_root_hygiene_checks(agentica_root_results)
    archive_counts, archive_exit = summarize_archive_boundary_checks(archive_results)
    telemetry_counts, telemetry_exit = summarize_telemetry_freshness_checks(telemetry_results)
    claude_arch_counts, claude_arch_exit = summarize_claude_arch_checks(claude_arch_results)

    meditation_ts_warn = sum(1 for r in meditation_ts_results if r["status"] == "WARN")
    meditation_ts_ok = sum(1 for r in meditation_ts_results if r["status"] == "OK")

    local_llm_warn = sum(1 for r in local_llm_results if r["status"] == "WARN")
    local_llm_ok = sum(1 for r in local_llm_results if r["status"] == "OK")

    # WARN-only family: a schema violation is the observation A3 is collecting,
    # so it must never gate the exit code (see _run_schema_violation_checks).
    schema_clean_warn = sum(1 for r in schema_clean_results if r["status"] == "WARN")
    schema_clean_ok = sum(1 for r in schema_clean_results if r["status"] == "OK")

    # claude-telemetry is a gating family: its FAIL feeds the exit code, unlike
    # the WARN-only meditation-timestamps / local-llm probes.
    claude_tel_fail = sum(1 for r in claude_tel_results if r["status"] == "FAIL")
    claude_tel_ok = sum(1 for r in claude_tel_results if r["status"] == "OK")

    total_ok = path_counts["OK"] + stale_counts["OK"] + live_source_counts["OK"] + runtime_counts["OK"] + root_counts["OK"] + agentica_root_counts["OK"] + archive_counts["OK"] + telemetry_counts["OK"] + meditation_ts_ok + local_llm_ok + schema_clean_ok + claude_tel_ok + claude_arch_counts["OK"]
    total_warn = path_counts["WARN"] + stale_counts["WARN"] + live_source_counts["WARN"] + runtime_counts["WARN"] + root_counts["WARN"] + agentica_root_counts["WARN"] + archive_counts["WARN"] + telemetry_counts["WARN"] + meditation_ts_warn + local_llm_warn + schema_clean_warn + claude_arch_counts["WARN"]
    total_fail = path_counts["FAIL"] + stale_counts["FAIL"] + live_source_counts["FAIL"] + runtime_counts["FAIL"] + root_counts["FAIL"] + agentica_root_counts["FAIL"] + archive_counts["FAIL"] + telemetry_counts["FAIL"] + claude_tel_fail + claude_arch_counts["FAIL"]
    exit_code = 1 if path_exit or stale_exit or live_source_exit or runtime_exit or root_exit or agentica_root_exit or archive_exit or telemetry_exit or claude_tel_fail or claude_arch_exit else 0

    print("--------------------")
    print(f"Summary: OK={total_ok} WARN={total_warn} FAIL={total_fail}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())