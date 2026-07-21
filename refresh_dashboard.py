#!/usr/bin/env python
"""Refresh the Governance dashboard: re-aggregate telemetry and copy the payload to
the dashboard's public dir so an open tab (polling) or a fresh load shows current data.

  python refresh_dashboard.py              # aggregate + copy (no history snapshot)
  python refresh_dashboard.py --snapshot   # also append a metrics_history snapshot

Run it often for freshness; pass --snapshot only occasionally (e.g. hourly) so the
trend/σ history window stays a stable baseline rather than a few minutes wide.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).resolve()
_GOV = _THIS.parent
# Order Samurai project root — same default as api/src/state.ts ORDER_SAMURAI_ROOT
_ORDER_SAMURAI_ROOT = Path(
    os.environ.get("ORDER_SAMURAI_ROOT", str(_GOV / "Order Samurai"))
)
_CODEX_EMIT = Path.home() / ".codex" / "scripts" / "codex_emit.py"
_PUBLIC_DIR = _GOV / "dashboard-ui" / "public"
_PUBLIC = _PUBLIC_DIR / "wid_payload.json"
_REPORTS_SRC = _THIS.parents[1] / "Data" / "reports"
_REPORTS_DST = _PUBLIC_DIR / "reports"


def _emit_mechanism_runs() -> None:
    """Emit a mechanism_run autonomic event for each NEW ReflexEngine skill run.

    The exec_log records every autonomous remediation (source=reflex_engine). Until
    now nothing turned those into mechanism_run autonomic events, so the consumer
    (Estimated_Cost_Savings) saw a permanent data gap. This bridges the real runs.

    routing_efficient is deliberately NOT set: the engine spawns `claude --print`
    on the default cloud model and records no route, so there is no truthful
    local-vs-cloud signal. Fabricating one would be the same dishonesty as the
    purged <synthetic> records. Events are emitted un-instrumented; the metric's
    data_gap stays True (keying on instrumented runs) until routing capture lands.

    Idempotent: a watermark file tracks the last emitted exec_log timestamp.
    """
    from agentica_core.telemetry import append_event
    exec_log = _ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl"
    if not exec_log.exists():
        return
    watermark_file = _GOV.parent / "Data" / "telemetry" / ".mechanism_run_watermark"
    last_ts = ""
    try:
        last_ts = watermark_file.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    newest = last_ts
    for line in exec_log.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        ts = entry.get("timestamp", "")
        if not ts or ts <= last_ts:
            continue
        event = {
            "timestamp": ts,
            "event": "mechanism_run",
            "skill": entry.get("skill"),
            "reflex_id": entry.get("reflex_id"),
            "source": entry.get("source", "reflex_engine"),
            # routing_efficient intentionally omitted — no truthful route signal yet
        }
        try:
            append_event({k: v for k, v in event.items() if v is not None})
        except Exception:
            break  # don't advance the watermark past an unwritten event
        if ts > newest:
            newest = ts
    if newest and newest != last_ts:
        try:
            watermark_file.parent.mkdir(parents=True, exist_ok=True)
            watermark_file.write_text(newest, encoding="utf-8")
        except OSError:
            pass


def main() -> int:
    snapshot = "--snapshot" in sys.argv
    sys.path.insert(0, str(_GOV))
    from agentica_core.aggregate import aggregate, write_payload

    # keep Codex telemetry complete (idempotent upsert; safe if Python/codex absent)
    if _CODEX_EMIT.exists():
        try:
            subprocess.run([sys.executable, str(_CODEX_EMIT)], timeout=60,
                           capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError):
            pass

    # Bridge real autonomous skill runs (exec_log) -> mechanism_run autonomic events.
    try:
        _emit_mechanism_runs()
    except Exception as _mr_err:
        print(f"WARN: mechanism_run emit failed: {_mr_err}", file=sys.stderr)

    # On snapshot runs, rebuild the weekly history series from telemetry (self-healing,
    # always complete), then aggregate() appends the current live row on top so the
    # series ends at now. Non-snapshot runs leave history untouched.
    import traceback

    if snapshot:
        try:
            from agentica_core.backfill_history import backfill
            backfill()
        except Exception:
            print("WARN: backfill failed", file=sys.stderr)
            traceback.print_exc()
        try:  # recalibrate thresholds from the refreshed weekly distribution
            from agentica_core.calibrate import calibrate
            calibrate()
        except Exception:
            print("WARN: calibrate failed", file=sys.stderr)
            traceback.print_exc()

    payload = aggregate(timestamp=datetime.now(timezone.utc).isoformat(), write_history=snapshot)

    # Inject cross-metric correlation reflexes (#7): synthetic CRITICAL/HIGH when multiple
    # metrics degrade simultaneously. Non-fatal — a broken correlation module never blocks refresh.
    try:
        from agentica_core.correlation import evaluate as _correlation_eval
        corr_reflexes = _correlation_eval(payload.get("pillars", {}))
        if corr_reflexes:
            existing = payload.get("reflexes") or []
            existing_ids = {r.get("id") for r in existing}
            new_entries = [r for r in corr_reflexes if r["id"] not in existing_ids]
            payload["reflexes"] = existing + new_entries
    except Exception as _corr_err:
        print(f"WARN: correlation engine failed: {_corr_err}", file=sys.stderr)

    # Compute per-skill efficacy (#G2 learning loop): reads exec_log.jsonl and writes
    # state/skill_efficacy.json.  ReflexEngine reads this file to apply longer cooldowns
    # to skills that consistently fail, reducing runaway retry noise.
    try:
        from agentica_core.skill_efficacy import compute as _efficacy_compute
        _efficacy_compute(
            log_path=_ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl",
            out_path=_ORDER_SAMURAI_ROOT / "state" / "skill_efficacy.json",
        )
    except Exception as _eff_err:
        print(f"WARN: skill efficacy compute failed: {_eff_err}", file=sys.stderr)

    # Stuck remediation report: identify skills that failed to improve their metrics
    # and generate per-entry recommendations for operator review.
    try:
        from agentica_core.skill_no_impact import analyze as _no_impact_analyze
        _stuck = _no_impact_analyze(
            log_path=_ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl",
            state_path=_ORDER_SAMURAI_ROOT / "state" / "reflex_engine_state.json",
        )
        if isinstance(payload.get("remediation_efficacy"), dict):
            payload["remediation_efficacy"]["stuck_remediations"] = _stuck
        else:
            payload["remediation_efficacy"] = {"stuck_remediations": _stuck}
    except Exception as _no_impact_err:
        print(f"WARN: skill_no_impact analysis failed: {_no_impact_err}", file=sys.stderr)

    # Write non_remediable_metrics.json: metrics marked auto_remediable=False in METRIC_CONFIG.
    # ReflexEngine reads this to skip autonomous queueing for metrics that require human action.
    try:
        from agentica_core.insights import METRIC_CONFIG as _MC_NR
        _non_remediable = sorted(
            mk for mk, cfg in _MC_NR.items() if cfg.get("auto_remediable") is False
        )
        _nr_path = _ORDER_SAMURAI_ROOT / "state" / "non_remediable_metrics.json"
        _nr_path.parent.mkdir(parents=True, exist_ok=True)
        _nr_path.write_text(json.dumps(_non_remediable, indent=2), encoding="utf-8")
    except Exception as _nr_err:
        print(f"WARN: non_remediable_metrics write failed: {_nr_err}", file=sys.stderr)

    # Write batch_metrics.json: the code-modifying, no-mechanism, non-urgent auto-remediable
    # metrics (insights.batch_deferred_metrics). ReflexEngine reads this to (1) verify-gate the
    # breach live before spawning the skill and (2) defer the spawn to REFLEX_BATCH_WINDOW —
    # verify real-time, improve overnight. Mirrors the non_remediable_metrics.json contract.
    try:
        from agentica_core.insights import batch_deferred_metrics as _batch_metrics
        _bm_path = _ORDER_SAMURAI_ROOT / "state" / "batch_metrics.json"
        _bm_path.parent.mkdir(parents=True, exist_ok=True)
        _bm_path.write_text(json.dumps(_batch_metrics(), indent=2), encoding="utf-8")
    except Exception as _bm_err:
        print(f"WARN: batch_metrics write failed: {_bm_err}", file=sys.stderr)

    # Write skill_metadata.json (#blast-radius): readonly vs code-modifying classification
    # derived from METRIC_CONFIG.  ReflexEngine reads this to apply tiered autonomy gates.
    try:
        from agentica_core.insights import METRIC_CONFIG as _MC
        _readonly: set[str] = set()
        _code_mod: set[str] = set()
        for _cfg in _MC.values():
            _sk = _cfg.get("skill")
            if not _sk:
                continue
            if _cfg.get("readonly", False):
                _readonly.add(_sk)
            else:
                _code_mod.add(_sk)
        # A skill appearing in any code-modifying metric is treated as code-modifying
        _readonly -= _code_mod
        _meta_path = _ORDER_SAMURAI_ROOT / "state" / "skill_metadata.json"
        _meta_path.parent.mkdir(parents=True, exist_ok=True)
        _meta_path.write_text(
            json.dumps(
                {"readonly": sorted(_readonly), "code_modifying": sorted(_code_mod)},
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as _meta_err:
        print(f"WARN: skill_metadata write failed: {_meta_err}", file=sys.stderr)

    # Reflex Eureka (#learning-loop): cross-correlate skill runs with metric improvement
    # and write ~/.claude/data/auto_eureka_skills.md for operator review and lesson capture.
    try:
        from agentica_core.reflex_eureka import analyze as _eureka_analyze
        _eureka_analyze(log_path=_ORDER_SAMURAI_ROOT / "state" / "exec_log.jsonl")
    except Exception as _eureka_err:
        print(f"WARN: reflex_eureka failed: {_eureka_err}", file=sys.stderr)

    src = write_payload(payload)
    _PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, _PUBLIC)

    # regenerate reports AFTER the payload exists (Sword section reads the fresh security snapshot)
    try:
        from agentica_core.weekly_report import generate
        generate(latest_only=not (snapshot or "--reports" in sys.argv))
    except Exception:
        print("WARN: weekly_report failed", file=sys.stderr)
        traceback.print_exc()
    try:
        from agentica_core.state_report import build_report, write_report
        write_report(build_report(payload=payload, timestamp=payload.get("timestamp")))
    except Exception:
        print("WARN: state_report failed", file=sys.stderr)
        traceback.print_exc()
    # mirror reports into the dashboard's public dir so the Reports tab can fetch them
    try:
        if _REPORTS_SRC.exists():
            _REPORTS_DST.mkdir(parents=True, exist_ok=True)
            for f in _REPORTS_SRC.glob("*"):
                if f.is_file():
                    shutil.copyfile(f, _REPORTS_DST / f.name)
    except Exception as _mirror_err:
        print(f"WARN: reports mirror failed: {_mirror_err}", file=sys.stderr)

    # if a built bundle exists (served deploy), mirror live data into it too so the
    # static build stays fresh without a rebuild (data is fetched at runtime, not bundled)
    dist = _GOV / "dashboard-ui" / "dist"
    try:
        if dist.exists():
            shutil.copyfile(_PUBLIC, dist / "wid_payload.json")
            if _REPORTS_DST.exists():
                (dist / "reports").mkdir(parents=True, exist_ok=True)
                for f in _REPORTS_DST.glob("*"):
                    if f.is_file():
                        shutil.copyfile(f, dist / "reports" / f.name)
    except Exception as _dist_err:
        print(f"WARN: dist mirror failed: {_dist_err}", file=sys.stderr)

    # Keep the zero-dependency static fallback in lockstep with the payload —
    # it silently drifted for 8 days when regeneration was manual-only.
    # Multi-window: compute 7d and all-time aggregations (render-only, no side effects)
    # and pass all three to the renderer for the Week/Month/Total segmented control.
    try:
        from agentica_core.render import write_dashboard
        ts = datetime.now(timezone.utc).isoformat()
        payload_7 = aggregate(timestamp=ts, window_days=7, write_history=False)
        payload_total = aggregate(timestamp=ts, window_days=36500, write_history=False)
        write_dashboard({"week": payload_7, "month": payload, "total": payload_total})
    except Exception as _render_err:
        print(f"WARN: dashboard.html render failed: {_render_err}", file=sys.stderr)

    # GOVERNANCE-001 bootstrap: the Governance_Review_Findings reflex can never
    # fire while the metric is SIMULATED, and the metric stays SIMULATED until
    # governance_review.py has produced findings at least once (chicken-and-egg).
    # On snapshot runs, if findings are absent or >7 days old, launch a detached
    # review so the loop self-primes without blocking the refresh.
    if snapshot:
        try:
            _findings = _GOV / "docs" / "governance_findings.json"
            _stale = (not _findings.exists()
                      or (datetime.now(timezone.utc).timestamp() - _findings.stat().st_mtime) > 7 * 86400)
            if _stale:
                subprocess.Popen([sys.executable, str(_GOV / "governance_review.py")],
                                 cwd=str(_GOV), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                print("refresh: governance_review bootstrap launched (findings absent/stale)")
        except Exception as _gov_err:
            print(f"WARN: governance_review bootstrap failed: {_gov_err}", file=sys.stderr)

    rc = payload.get("record_counts", {})
    print(f"refresh: payload -> {_PUBLIC} | snapshot={snapshot} | records={rc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
