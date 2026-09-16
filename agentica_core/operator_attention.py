"""operator_attention — the dashboard's "what needs a human" section (coverage review, W6).

The Order Samurai dashboard rendered graded metrics and reflexes only. Everything the
2026-09-02 revamp added — doctor rows, fleet escalation tiers, HITL items expired without
decision, auto-disabled jobs, backlog/goal SLAs — reached the human through banners and
the daily digest but had no place on the one screen they actually look at. This builds
one payload section, `wid_payload.operator_attention`, from the same files those
carriers read, so the dashboard can never disagree with the digest.

Total by construction: every reader is wrapped, and a reader that cannot answer
contributes a `data_gap` entry (what could not be measured, and why) instead of a
silently empty list. An empty section is "measured and clear"; a `data_gap` is "unknown".
refresh_dashboard.py calls `aggregate()` unguarded, so this module must never raise
(Sol 5.6 plan review, finding 6).

Thresholds (fleet tiers, SLA days) are imported from their owners — bin/hitl_alerts.py and
execution/escalation_sla.py — never restated here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_OS_ROOT = Path(os.environ.get(
    "ORDER_SAMURAI_ROOT",
    str(Path(__file__).resolve().parent.parent / "Order Samurai"),
))

DOCTOR_STALE_HOURS = 36.0   # mirrors hitl_alerts.DOCTOR_STALE_HOURS; re-read from the module when loadable
PENDING_SHOWN = 8
EXPIRED_SHOWN = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        t = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _read_json(path: Path) -> tuple[Any, str | None]:
    """(parsed, None) or (None, reason)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path.name} does not exist"
    except (OSError, ValueError) as exc:
        return None, f"{path.name} unreadable: {exc}"


def _load_hitl_alerts(os_root: Path):
    """bin/hitl_alerts.py by path (it is a script, not a package); None when it cannot load."""
    path = os_root / "bin" / "hitl_alerts.py"
    try:
        spec = importlib.util.spec_from_file_location("hitl_alerts_for_dashboard", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 — a broken carrier must not break the payload
        return None


def _load_escalation_sla(os_root: Path):
    try:
        if str(os_root) not in sys.path:
            sys.path.insert(0, str(os_root))
        from execution import escalation_sla  # type: ignore
        return escalation_sla
    except Exception:  # noqa: BLE001
        return None


# ── sections ────────────────────────────────────────────────────────────────────

def doctor_section(os_root: Path, now: datetime, gaps: list[str]) -> dict:
    data, why = _read_json(os_root / "state" / "doctor_last.json")
    if why:
        gaps.append(f"doctor: {why} — the daily com.agentica.os-doctor job has produced no readable result")
        return {"available": False}
    gen = _parse_ts(data.get("generated_at")) if isinstance(data, dict) else None
    fails = data.get("fails") if isinstance(data, dict) else None
    warns = data.get("warns") if isinstance(data, dict) else None
    counts = data.get("counts") if isinstance(data, dict) else None
    if not isinstance(fails, list) or not isinstance(counts, dict):
        gaps.append("doctor: doctor_last.json has an unexpected shape (no counts/fails)")
        return {"available": False}
    if gen is None:
        gaps.append("doctor: doctor_last.json carries no readable generated_at")
    age_h = (now - gen).total_seconds() / 3600.0 if gen else None
    if warns is None:
        # A pre-2026-09-02 producer: fails only. Say so rather than showing "0 warnings".
        gaps.append("doctor: this doctor_last.json predates WARN persistence — WARN rows unknown")
    elif isinstance(warns, list) and isinstance(counts.get("WARN"), int) and counts["WARN"] > len(warns):
        gaps.append(f"doctor: count says {counts['WARN']} WARN but only {len(warns)} row(s) were persisted")
    if isinstance(counts.get("FAIL"), int) and counts["FAIL"] > len(fails):
        gaps.append(f"doctor: count says {counts['FAIL']} FAIL but only {len(fails)} row(s) were persisted")
    return {
        "available": True,
        "generated_at": gen.isoformat() if gen else None,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "stale": bool(age_h is not None and age_h > DOCTOR_STALE_HOURS),
        "counts": {k: counts.get(k) for k in ("OK", "WARN", "FAIL")},
        "fails": [r for r in fails if isinstance(r, dict)],
        "warns": [r for r in warns if isinstance(r, dict)] if isinstance(warns, list) else None,
    }


def fleet_section(os_root: Path, now: datetime, hitl_alerts, gaps: list[str]) -> dict:
    probe, why = _read_json(os_root.parent / "data" / "fleet_probe.json")
    if why:
        gaps.append(f"fleet: {why} — bin/fleet_probe.py has not written a result")
        return {"available": False}
    if not isinstance(probe, dict) or not isinstance(probe.get("failing_jobs"), list):
        gaps.append("fleet: fleet_probe.json has an unexpected shape")
        return {"available": False}
    gen = _parse_ts(probe.get("generated_at"))
    if gen is None:
        gaps.append("fleet: fleet_probe.json carries no readable generated_at — freshness unknown")
    age_h = (now - gen).total_seconds() / 3600.0 if gen else None
    stale_after = getattr(hitl_alerts, "FLEET_PROBE_STALE_HOURS", 3.0) if hitl_alerts else 3.0
    state, state_why = _read_json(os_root / "state" / "hitl_alert_state.json")
    state_ok = state_why is None and isinstance(state, dict)
    if not state_ok:
        gaps.append(f"fleet: {state_why or 'hitl_alert_state.json has an unexpected shape'} — days/tiers unknown")
        state = {}
    failing = sorted(str(j) for j in probe["failing_jobs"])
    tiers: dict[str, dict] = {}
    if hitl_alerts is not None and state_ok:
        try:
            tiers = hitl_alerts.fleet_tiers(hitl_alerts.fleet_days_from_state(state, failing, now))
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"fleet: tier computation failed ({type(exc).__name__}) — days/tiers unknown")
    else:
        gaps.append("fleet: bin/hitl_alerts.py could not be loaded — escalation tiers unknown")
    return {
        "available": True,
        "generated_at": gen.isoformat() if gen else None,
        "stale": gen is None or bool(age_h is not None and age_h > stale_after),
        "failing": [{"job": j, "days": tiers.get(j, {}).get("days"), "tier": tiers.get(j, {}).get("tier")}
                    for j in failing],
        "unreachable": sorted(str(s) for s in (probe.get("unreachable_services") or [])),
    }


def _age_days(now: datetime, raw: Any) -> int | None:
    t = _parse_ts(raw)
    return (now - t).days if t else None


def decisions_section(os_root: Path, repo_root: Path, now: datetime, hitl_alerts, sla, gaps: list[str]) -> dict:
    out: dict[str, Any] = {"available": True}
    queue, why = _read_json(os_root / "state" / "hitl_queue.json")
    items = queue.get("items") if isinstance(queue, dict) else queue
    if why or not isinstance(items, list):
        gaps.append(f"decisions: {why or 'hitl_queue.json has no items list'} — approvals unknown")
        out.update(pending=None, expired_undecided=None)
    else:
        rows = [i for i in items if isinstance(i, dict)]
        pending = sorted((i for i in rows if i.get("status") == "pending"),
                         key=lambda i: str(i.get("enqueued_at") or ""))
        expired = sorted((i for i in rows if i.get("status") == "expired"),
                         key=lambda i: str(i.get("expired_at") or ""))
        out["pending"] = {
            "count": len(pending),
            "items": [{"id": i.get("id"), "source": i.get("source"), "skill": i.get("skill"),
                       "command": i.get("command"), "pillar": i.get("pillar"),
                       "waiting_days": _age_days(now, i.get("enqueued_at")),
                       "expires_at": i.get("expires_at")} for i in pending[:PENDING_SHOWN]],
        }
        out["expired_undecided"] = {
            "count": len(expired),
            "items": [{"id": i.get("id"), "source": i.get("source"), "skill": i.get("skill"),
                       "pillar": i.get("pillar"), "expired_days_ago": _age_days(now, i.get("expired_at"))}
                      for i in expired[:EXPIRED_SHOWN]],
            "decide_with": "python3 bin/bushido_check.py --retire <id> --reason '…'",
        }
    # Auto-disabled jobs (R4.2 sweep) — recent window per the digest's own constant.
    if hitl_alerts is not None and hasattr(hitl_alerts, "load_retirements"):
        try:
            hitl_alerts.RETIREMENTS_PATH = os_root / "state" / "fleet_retirements.json"
            hitl_alerts._now = lambda: now  # noqa: SLF001 — same clock as this build
            rec = hitl_alerts.load_retirements()
            out["auto_disabled"] = ([] if rec is None else
                                    [r for r in rec if isinstance(r, dict) and "error" not in r])
            if rec and any("error" in r for r in rec):
                gaps.append("decisions: fleet_retirements.json unreadable")
            # A rollback that never verified is machine state nobody decided on, so it
            # is an attention item in its own right — not part of auto_disabled, which
            # is jobs a decision retired.
            stuck = hitl_alerts.load_unreconciled_retirements()
            out["rollback_incomplete"] = stuck
            for r in stuck:
                gaps.append(f"decisions: {r.get('label')} may still be disabled after a failed "
                            f"rollback — revert: {r.get('revert')}")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"decisions: retirements reader failed ({type(exc).__name__})")
            out["auto_disabled"] = None
            out["rollback_incomplete"] = None
    else:
        out["auto_disabled"] = None
        out["rollback_incomplete"] = None
        gaps.append("decisions: bin/hitl_alerts.py could not be loaded — auto-disabled jobs unknown")
    # Backlog + goal SLAs: the same rows the escalation-sla doctor family emits.
    if sla is None:
        gaps.append("decisions: execution/escalation_sla.py could not be loaded — SLA counts unknown")
        out.update(backlog_overdue=None, goals_undated_or_overdue=None)
    else:
        try:
            b = sla.backlog_checks(os_root / "state" / "PROPOSED_BACKLOG.json", now)
            g = sla.goal_checks(repo_root / ".planning" / "GOAL_REGISTRY.jsonl", now)
            out["backlog_overdue"] = None if any("cannot measure" in r["detail"] for r in b) else \
                sum(1 for r in b if r["status"] == "WARN")
            out["goals_undated_or_overdue"] = None if any("cannot measure" in r["detail"] for r in g) else \
                sum(1 for r in g if r["status"] == "WARN")
            for rows_, name in ((b, "backlog"), (g, "goals")):
                for r in rows_:
                    if "cannot measure" in r["detail"]:
                        gaps.append(f"decisions: {name} — {r['detail']}")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"decisions: SLA reader failed ({type(exc).__name__})")
            out.update(backlog_overdue=None, goals_undated_or_overdue=None)
    return out


# ── entry points ────────────────────────────────────────────────────────────────

def build(os_root: Path | None = None, repo_root: Path | None = None,
          now: datetime | None = None) -> dict:
    os_root = os_root or _OS_ROOT
    repo_root = repo_root or os_root.parents[1]
    now = now or _now()
    gaps: list[str] = []
    hitl_alerts = _load_hitl_alerts(os_root)
    sla = _load_escalation_sla(os_root)
    global DOCTOR_STALE_HOURS  # noqa: PLW0603 — one owner for the number, read at build time
    if hitl_alerts is not None and hasattr(hitl_alerts, "DOCTOR_STALE_HOURS"):
        DOCTOR_STALE_HOURS = float(hitl_alerts.DOCTOR_STALE_HOURS)
    section = {
        "generated_at": now.isoformat(),
        "doctor": doctor_section(os_root, now, gaps),
        "fleet": fleet_section(os_root, now, hitl_alerts, gaps),
        "decisions": decisions_section(os_root, repo_root, now, hitl_alerts, sla, gaps),
    }
    section["data_gap"] = gaps
    return section


def build_safe(**kwargs) -> dict:
    """`build`, but a crash becomes a section that says it crashed. This is the only
    entry point aggregate() may call."""
    try:
        return build(**kwargs)
    except Exception as exc:  # noqa: BLE001
        return {
            "generated_at": _now().isoformat(),
            "doctor": {"available": False},
            "fleet": {"available": False},
            "decisions": {"available": False},
            "data_gap": [f"operator_attention builder crashed: {type(exc).__name__}: {exc}"],
        }
