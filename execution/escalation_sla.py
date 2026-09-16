#!/usr/bin/env python3
"""escalation_sla — every open escalation carries a date, or doctor says so (R4.3, 2026-09-02).

Coverage review §6 Rz2: escalation to a human expires instead of resolving. Thirty-four
PROPOSED_BACKLOG rows sat `proposed` with no owner or date, thirty-one goals had no due
date, twelve HITL items expired unreviewed and were never decided. Nothing asked.

WARN-only (spectator), read-only over three files. One row per overdue thing, so the count
in doctor is the count of decisions owed; an unreadable input is a WARN naming it, never
an OK. The mutations these rows call for happen through each file's owning writer
(`bin/ronin promote`, `/goal-register`, `bin/bushido_check.py --retire`), not here.

  backlog  `status: proposed` rows older than `backlog_days` (by created_at, else the
           YYYY-MM-DD prefix of triaged_at); undated proposed rows are reported as such.
  goals    open goals with neither `due_date` nor `no_due_date_reason`; open goals whose
           due_date has passed.
  hitl     `status: expired` items older than `expired_days` with no decision recorded
           (R4.1: they stay in the digest; this is the same fact as a doctor row).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

LABEL = "escalation-sla"
BACKLOG_DAYS = 14
EXPIRED_DAYS = 7


def _row(status: str, label: str, detail: str) -> dict:
    return {"status": status, "label": f"{LABEL}.{label}", "detail": detail}


def _parse(ts) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    for candidate in (ts, ts[:10]):
        try:
            t = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return None


def backlog_checks(path: Path, now: datetime, max_days: int = BACKLOG_DAYS) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("no items list")
    except (OSError, ValueError) as exc:
        return [_row("WARN", "backlog", f"{path.name} unreadable ({exc}); cannot measure")]
    rows: list[dict] = []
    proposed = [i for i in items if isinstance(i, dict) and i.get("status") == "proposed"]
    for it in proposed:
        created = _parse(it.get("created_at")) or _parse(it.get("triaged_at"))
        ident = it.get("id") or "?"
        if created is None:
            rows.append(_row("WARN", f"backlog.{ident}",
                             f"proposed with no created_at/triaged_at — age unknown; decide or date it "
                             f"({str(it.get('title') or '')[:60]})"))
            continue
        age = (now - created).days
        if age > max_days:
            rows.append(_row("WARN", f"backlog.{ident}",
                             f"proposed for {age}d (> {max_days}d SLA) with no decision — "
                             f"promote (bin/ronin promote), reject, or expire it "
                             f"({str(it.get('title') or '')[:60]})"))
    if not rows:
        rows.append(_row("OK", "backlog", f"{len(proposed)} proposed row(s), none past the {max_days}d SLA"))
    return rows


def goal_checks(path: Path, now: datetime) -> list[dict]:
    try:
        goals = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError) as exc:
        return [_row("WARN", "goals", f"{path.name} unreadable ({exc}); cannot measure")]
    rows: list[dict] = []
    open_goals = [g for g in goals if isinstance(g, dict) and not g.get("completed")]
    for g in open_goals:
        ident = g.get("id") or "?"
        due_raw = g.get("due_date")
        due = _parse(due_raw)
        if due is None:
            if g.get("no_due_date_reason"):
                continue
            rows.append(_row("WARN", f"goal.{ident}",
                             "open goal with no due_date and no no_due_date_reason — "
                             "an undated goal is never overdue and never done"))
        elif ((isinstance(due_raw, str) and len(due_raw) == 10 and due.date() < now.date())
              or (not (isinstance(due_raw, str) and len(due_raw) == 10) and due < now)):
            rows.append(_row("WARN", f"goal.{ident}",
                             f"past due by {(now - due).days}d (due {due.date().isoformat()})"))
    if not rows:
        rows.append(_row("OK", "goals", f"{len(open_goals)} open goal(s), all dated and within their due date"))
    return rows


def hitl_checks(path: Path, now: datetime, max_days: int = EXPIRED_DAYS) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("no items list")
    except (OSError, ValueError) as exc:
        return [_row("WARN", "hitl", f"{path.name} unreadable ({exc}); cannot measure")]
    rows: list[dict] = []
    expired = [i for i in items if isinstance(i, dict) and i.get("status") == "expired"]
    for it in expired:
        t = _parse(it.get("expired_at"))
        age = (now - t).days if t else None
        if age is None or age > max_days:
            rows.append(_row("WARN", f"hitl.{it.get('id') or '?'}",
                             f"expired {age if age is not None else '?'}d ago with no decision "
                             f"({it.get('skill') or it.get('command') or '?'}) — "
                             f"bin/bushido_check.py --retire {it.get('id')} --reason '…'"))
    if not rows:
        rows.append(_row("OK", "hitl", f"{len(expired)} expired item(s), none older than {max_days}d undecided"))
    return rows


def run_checks(*, os_root: Path | None = None, repo_root: Path | None = None,
               now: datetime | None = None) -> list[dict]:
    os_root = os_root or Path(__file__).resolve().parents[1]
    repo_root = repo_root or os_root.parents[1]
    now = now or datetime.now(timezone.utc)
    return (backlog_checks(os_root / "state" / "PROPOSED_BACKLOG.json", now)
            + goal_checks(repo_root / ".planning" / "GOAL_REGISTRY.jsonl", now)
            + hitl_checks(os_root / "state" / "hitl_queue.json", now))


if __name__ == "__main__":
    for r in run_checks():
        print(f"[{r['status']}] {r['label']}: {r['detail']}")
