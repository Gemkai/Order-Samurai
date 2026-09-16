"""escalation-sla doctor family (coverage review R4.3): every open escalation carries a date."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import escalation_sla as sla  # noqa: E402

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


# ── backlog ───────────────────────────────────────────────────────────────────

def test_backlog_rows_past_the_sla_warn_one_row_each(tmp_path):
    p = _write(tmp_path / "PROPOSED_BACKLOG.json", {"items": [
        {"id": "A", "status": "proposed", "created_at": _iso(30), "title": "old one"},
        {"id": "B", "status": "proposed", "triaged_at": f"{(NOW - timedelta(days=20)).date()} by x"},
        {"id": "C", "status": "proposed", "created_at": _iso(3)},
        {"id": "D", "status": "resolved", "created_at": _iso(90)},
        {"id": "E", "status": "proposed"},
    ]})
    rows = sla.backlog_checks(p, NOW)
    assert [r["label"] for r in rows] == ["escalation-sla.backlog.A", "escalation-sla.backlog.B",
                                         "escalation-sla.backlog.E"]
    assert "proposed for 30d (> 14d SLA)" in rows[0]["detail"] and "old one" in rows[0]["detail"]
    assert "proposed for 20d" in rows[1]["detail"]
    assert "age unknown" in rows[2]["detail"]


def test_backlog_within_sla_is_ok(tmp_path):
    p = _write(tmp_path / "PROPOSED_BACKLOG.json", {"items": [{"id": "C", "status": "proposed", "created_at": _iso(3)}]})
    assert sla.backlog_checks(p, NOW) == [{"status": "OK", "label": "escalation-sla.backlog",
                                          "detail": "1 proposed row(s), none past the 14d SLA"}]


def test_unreadable_backlog_cannot_measure(tmp_path):
    rows = sla.backlog_checks(tmp_path / "missing.json", NOW)
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]
    rows = sla.backlog_checks(_write(tmp_path / "bad.json", "{not json"), NOW)
    assert rows[0]["status"] == "WARN"


# ── goals ─────────────────────────────────────────────────────────────────────

def test_goals_without_a_date_or_past_due_warn(tmp_path):
    p = _write(tmp_path / "GOAL_REGISTRY.jsonl", "\n".join(json.dumps(g) for g in [
        {"id": "undated", "completed": False},
        {"id": "excused", "completed": False, "no_due_date_reason": "standing goal"},
        {"id": "overdue", "completed": False, "due_date": (NOW - timedelta(days=4)).date().isoformat()},
        {"id": "future", "completed": False, "due_date": (NOW + timedelta(days=4)).date().isoformat()},
        {"id": "done", "completed": True},
    ]))
    rows = sla.goal_checks(p, NOW)
    assert [r["label"] for r in rows] == ["escalation-sla.goal.undated", "escalation-sla.goal.overdue"]
    assert "no due_date and no no_due_date_reason" in rows[0]["detail"]
    assert "past due by 4d" in rows[1]["detail"]


def test_all_goals_dated_is_ok(tmp_path):
    p = _write(tmp_path / "GOAL_REGISTRY.jsonl", json.dumps(
        {"id": "future", "completed": False, "due_date": (NOW + timedelta(days=4)).date().isoformat()}))
    assert [r["status"] for r in sla.goal_checks(p, NOW)] == ["OK"]


def test_date_only_goal_is_not_overdue_until_the_next_calendar_day(tmp_path):
    p = _write(tmp_path / "GOAL_REGISTRY.jsonl", json.dumps(
        {"id": "today", "completed": False, "due_date": NOW.date().isoformat()}))
    assert [r["status"] for r in sla.goal_checks(p, NOW)] == ["OK"]


def test_missing_goal_registry_cannot_measure(tmp_path):
    rows = sla.goal_checks(tmp_path / "GOAL_REGISTRY.jsonl", NOW)
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]


# ── hitl ──────────────────────────────────────────────────────────────────────

def test_expired_hitl_items_older_than_a_week_warn_with_the_retire_command(tmp_path):
    p = _write(tmp_path / "hitl_queue.json", {"items": [
        {"id": "hitl-old", "status": "expired", "expired_at": _iso(45), "skill": "pip-safe-upgrade"},
        {"id": "hitl-new", "status": "expired", "expired_at": _iso(2)},
        {"id": "hitl-ret", "status": "retired", "expired_at": _iso(45)},
        {"id": "hitl-undated", "status": "expired"},
    ]})
    rows = sla.hitl_checks(p, NOW)
    assert [r["label"] for r in rows] == ["escalation-sla.hitl.hitl-old", "escalation-sla.hitl.hitl-undated"]
    assert "expired 45d ago with no decision (pip-safe-upgrade) — bin/bushido_check.py --retire hitl-old" in rows[0]["detail"]


def test_no_stale_expired_items_is_ok(tmp_path):
    p = _write(tmp_path / "hitl_queue.json", {"items": [{"id": "x", "status": "expired", "expired_at": _iso(1)}]})
    assert [r["status"] for r in sla.hitl_checks(p, NOW)] == ["OK"]


# ── composition ───────────────────────────────────────────────────────────────

def test_run_checks_reads_the_three_files_from_the_roots(tmp_path):
    os_root = tmp_path / "Governance" / "Order Samurai"
    _write(os_root / "state" / "PROPOSED_BACKLOG.json", {"items": []})
    _write(os_root / "state" / "hitl_queue.json", {"items": []})
    _write(tmp_path / ".planning" / "GOAL_REGISTRY.jsonl", "")
    rows = sla.run_checks(os_root=os_root, repo_root=tmp_path, now=NOW)
    assert [r["status"] for r in rows] == ["OK", "OK", "OK"]
