"""Bushido Engine — unified tier-decision module for Order Samurai.

Single source of truth that the TS Reflex Engine (via bin/bushido_check.py
subprocess) and the SENSEI dojo cycle both route through. Decides whether a
work item fires automatically, gets enqueued for review, requires explicit
human approval, or is blocked outright.

Decision matrix (compute_tier):

                       | Low blast_radius   | High blast_radius
                       | (confined)         | (repo / system)
    -------------------+--------------------+--------------------
    Reversible         | AUTO               | QUEUE
    Irreversible       | HITL               | HARD_STOP

ronin_mode collapses AUTO + QUEUE + HITL -> AUTO. HARD_STOP is permanent.
blast_radius=IRREVERSIBLE is HARD_STOP regardless of reversible (encodes
git push, unreplicated delete, budget overrun, etc.).

Stdlib only; no external dependencies.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Public enums ──────────────────────────────────────────────────────────────

class Tier(str, Enum):
    AUTO = "auto"
    QUEUE = "queue"
    HITL = "hitl"
    HARD_STOP = "hard_stop"


class BlastRadius(str, Enum):
    CONFINED = "confined"       # state/*.json only
    REPO = "repo"               # edits within the Order Samurai repo
    SYSTEM = "system"           # writes outside the repo (~/.claude, etc.)
    IRREVERSIBLE = "irreversible"  # cost commitment, push, unreplicated delete


# ── Work item ─────────────────────────────────────────────────────────────────

@dataclass
class WorkItem:
    """Unified type that both a Reflex breach and a Dojo backlog item map to."""
    skill: str = ""
    source: str = ""              # "reflex" | "dojo" | other
    command: str = ""
    blast_radius: BlastRadius = BlastRadius.REPO
    reversible: bool = True
    id: str = ""
    metric_id: str | None = None
    pillar: str | None = None
    backlog_id: str | None = None
    consecutive_no_improvement: int = 0
    stuck: bool = False
    context: str = ""
    pillar_ronin_mode: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ── Core tier computation (pure, no I/O) ──────────────────────────────────────

def compute_tier(work_item: WorkItem, ronin_mode: bool = False) -> Tier:
    """Pure 2-axis matrix. No file I/O, no env reads — easy to test.

    Hard limits encoded into blast_radius/reversible take precedence over
    ronin_mode: an IRREVERSIBLE op never collapses to AUTO.
    """
    blast = work_item.blast_radius
    reversible = bool(work_item.reversible)

    # Irreversible blast always hard-stops (git push, unreplicated delete, etc.)
    if blast == BlastRadius.IRREVERSIBLE:
        return Tier.HARD_STOP

    # Irreversible action on repo/system blast: too costly to auto-fire.
    if not reversible and blast in (BlastRadius.REPO, BlastRadius.SYSTEM):
        return Tier.HARD_STOP

    if reversible:
        tier = Tier.AUTO if blast == BlastRadius.CONFINED else Tier.QUEUE
    else:
        # Irreversible + confined (e.g. delete a state file in the queue) → HITL
        tier = Tier.HITL

    if ronin_mode and tier in (Tier.QUEUE, Tier.HITL):
        return Tier.AUTO

    return tier


# ── Hard limits (runtime state — budget, etc.) ────────────────────────────────

def _over_daily_budget(repo_root: Path) -> bool:
    """Read state/budget_ledger.json; fail open (False) on any error.

    Compares ledger.date to today (UTC). Different date → not over (new day).
    """
    try:
        ledger_path = Path(repo_root) / "state" / "budget_ledger.json"
        d = json.loads(ledger_path.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if d.get("date") != today:
            return False
        spent = float(d.get("spent_usd", 0) or 0)
        limit = float(d.get("daily_limit_usd", 5.0) or 5.0)
        return spent >= limit
    except Exception:
        return False


def _is_hard_limit(work_item: WorkItem, repo_root: Path | None = None) -> bool:
    """Runtime hard-limit check. Superset of compute_tier's HARD_STOP."""
    # Matrix-driven hard stops
    if work_item.blast_radius == BlastRadius.IRREVERSIBLE:
        return True
    if not work_item.reversible and work_item.blast_radius in (
        BlastRadius.REPO, BlastRadius.SYSTEM,
    ):
        return True
    # Runtime: budget
    if repo_root and _over_daily_budget(Path(repo_root)):
        return True
    return False


# ── Skill metadata + work-item construction ───────────────────────────────────

def load_skill_metadata(repo_root: Path) -> dict[str, dict]:
    """Read state/skill_tiers.json:skills. Returns {} on any error.

    NOTE: reads skill_tiers.json (not skill_metadata.json). The latter is
    overwritten on every refresh_dashboard.py run with only readonly /
    code_modifying arrays for the TS engine.
    """
    try:
        path = Path(repo_root) / "state" / "skill_tiers.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        skills = data.get("skills", {})
        return skills if isinstance(skills, dict) else {}
    except Exception:
        return {}


def skill_to_work_item(
    skill_name: str,
    source: str,
    repo_root: Path,
    **kwargs: Any,
) -> WorkItem:
    """Build a WorkItem for `skill_name`, looking up tier metadata.

    Unknown skill → blast_radius=REPO, reversible=True → QUEUE (safe).
    """
    metadata = load_skill_metadata(Path(repo_root))
    meta = metadata.get(skill_name, {})
    blast_str = meta.get("blast_radius", "repo")
    reversible = bool(meta.get("reversible", True))

    try:
        blast = BlastRadius(blast_str)
    except ValueError:
        blast = BlastRadius.REPO

    command = kwargs.pop("command", f"/{skill_name}")
    return WorkItem(
        skill=skill_name,
        source=source,
        command=command,
        blast_radius=blast,
        reversible=reversible,
        **kwargs,
    )


# ── Ronin mode resolution ─────────────────────────────────────────────────────

_RONIN_TRUTHY = {"1", "true", "yes", "ronin", "on"}


def _is_ronin(value: Any) -> bool:
    return str(value).strip().lower() in _RONIN_TRUTHY


def resolve_ronin_mode(
    pillar: str | None,
    repo_root: Path,
    global_override: bool | None = None,
) -> bool:
    """Single authority for "is ronin mode on?".

    Priority (first match wins):
      1. global_override parameter (callers may force a value for testing)
      2. env BUSHIDO_RONIN_GLOBAL ("true"/"1"/"ronin"/"on" -> True, else False)
      3. DOJO_STATE.json top-level "ronin_mode" ("ronin" -> True, else False)
      4. DOJO_STATE.json pillars[pillar].ronin_mode
      5. False
    """
    if global_override is not None:
        return bool(global_override)

    env_val = os.environ.get("BUSHIDO_RONIN_GLOBAL")
    if env_val is not None and env_val != "":
        return _is_ronin(env_val)

    try:
        state_path = Path(repo_root) / "state" / "DOJO_STATE.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    # Top-level only OVERRIDES when it's explicitly "ronin". A "dormant" (or any
    # non-ronin) value at the top level falls through to per-pillar — matches the
    # Phase 2.3 spec: "When absent or 'dormant', per-pillar settings apply."
    top = state.get("ronin_mode")
    if top is not None and _is_ronin(top):
        return True

    if pillar:
        pillars = state.get("pillars", {}) or {}
        per = pillars.get(pillar, {}).get("ronin_mode") if isinstance(pillars, dict) else None
        if per is not None:
            return _is_ronin(per)

    return False


# ── HITL queue I/O ────────────────────────────────────────────────────────────

def _approval_key(work_item: WorkItem) -> tuple[str, str, str, str, str]:
    """R4: approval key = (source, skill, pillar, metric_id, backlog_id).
    Empty strings for None to make matching deterministic.
    """
    return (
        work_item.source or "",
        work_item.skill or "",
        work_item.pillar or "",
        work_item.metric_id or "",
        work_item.backlog_id or "",
    )


def _item_key(item: dict) -> tuple[str, str, str, str, str]:
    return (
        item.get("source") or "",
        item.get("skill") or "",
        item.get("pillar") or "",
        item.get("metric_id") or "",
        item.get("backlog_id") or "",
    )


def _atomic_write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        # Windows: a reader may hold the destination open; fall back to copy
        import shutil
        shutil.copyfile(tmp, path)
        try:
            tmp.unlink()
        except OSError:
            pass


def _load_queue(repo_root: Path) -> dict:
    path = Path(repo_root) / "state" / "hitl_queue.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("hitl_queue.json root must be an object")
        data.setdefault("schema_version", 1)
        data.setdefault("items", [])
        return data
    except (OSError, ValueError):
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": 1,
            "created_at": now,
            "updated_at": now,
            "items": [],
        }


def enqueue_hitl(work_item: WorkItem, tier: Tier, repo_root: Path) -> str:
    """Insert into hitl_queue.json. Idempotent on _approval_key under "pending".

    Returns the queue item ID (existing one if duplicate, new uuid otherwise).
    """
    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    data = _load_queue(repo_root)
    items = data["items"]
    target_key = _approval_key(work_item)

    for item in items:
        if item.get("status") == "pending" and _item_key(item) == target_key:
            return item["id"]  # idempotent

    now = datetime.now(timezone.utc).isoformat()
    new_id = f"hitl-{uuid.uuid4().hex[:8]}"
    tier_val = tier.value if isinstance(tier, Tier) else str(tier)
    blast_val = (
        work_item.blast_radius.value
        if isinstance(work_item.blast_radius, BlastRadius)
        else str(work_item.blast_radius)
    )

    items.append({
        "id": new_id,
        "source": work_item.source,
        "tier_assigned": tier_val,
        "status": "pending",
        "enqueued_at": now,
        "approved_at": None,
        "rejected_at": None,
        "rejected_reason": None,
        "executing_at": None,
        "completed_at": None,
        "skill": work_item.skill,
        "command": work_item.command,
        "metric_id": work_item.metric_id,
        "pillar": work_item.pillar,
        "blast_radius": blast_val,
        "reversible": work_item.reversible,
        "consecutive_no_improvement": work_item.consecutive_no_improvement,
        "stuck": work_item.stuck,
        "context": work_item.context,
        "backlog_id": work_item.backlog_id,
    })
    data["updated_at"] = now
    _atomic_write_json(queue_path, data)
    return new_id


def _consume_approval(work_item: WorkItem, repo_root: Path) -> str | None:
    """If an `approved` entry matches this work item's key, mark it `executing`.

    Returns the consumed item's id (str) on success, or None if no matching
    approval was found. Phase 3.4 uses the returned id to drive `--complete`
    once the skill finishes. Key per R4:
    (source, skill, pillar, metric_id, backlog_id).
    """
    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    items = data.get("items", [])
    if not isinstance(items, list):
        return None

    target_key = _approval_key(work_item)
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "approved":
            continue
        if _item_key(item) != target_key:
            continue
        item["status"] = "executing"
        item["executing_at"] = now
        data["updated_at"] = now
        _atomic_write_json(queue_path, data)
        return item.get("id")
    return None


def mark_complete(queue_id: str, repo_root: Path, failed: bool = False) -> bool:
    """Mark a queue item `done` (or `failed`). Called by SENSEI step F or by the
    TS Reflex Engine in _afterRun(). Returns True iff the item was found.
    """
    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False

    items = data.get("items", [])
    if not isinstance(items, list):
        return False

    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("id") != queue_id:
            continue
        item["status"] = "failed" if failed else "done"
        item["completed_at"] = now
        data["updated_at"] = now
        _atomic_write_json(queue_path, data)
        return True
    return False


# ── Decision audit log ────────────────────────────────────────────────────────

def _emit_decision(
    work_item: WorkItem,
    tier: Tier,
    repo_root: Path,
    queue_id: str | None = None,
    consumed: bool = False,
) -> None:
    """Best-effort audit line for every NON-plain-AUTO decision.

    Appends one JSON object to state/autonomic_events.jsonl — the repo-local
    stream the kill-chain scout also appends to directly (NOT the gated
    Governance emitter, which has a closed event-type allow-list). Plain AUTO is
    intentionally not logged to avoid high-frequency noise; an AUTO that came
    from consuming an approval IS logged (a human action fired). Never raises —
    audit logging must not break a decision.
    """
    try:
        tier_val = tier.value if isinstance(tier, Tier) else str(tier)
        detail = f"{work_item.skill or '?'} -> {tier_val}" + (
            " (approval consumed)" if consumed else ""
        )
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "bushido_decision",
            "tier": tier_val,
            "skill": work_item.skill,
            "source": work_item.source,
            "detail": detail,
        }
        if work_item.pillar:
            event["pillar"] = work_item.pillar
        if work_item.metric_id:
            event["metric_id"] = work_item.metric_id
        if queue_id:
            event["queue_id"] = queue_id
        path = Path(repo_root) / "state" / "autonomic_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception:
        pass


# ── Single entry point ────────────────────────────────────────────────────────

def decide(
    work_item: WorkItem,
    repo_root: Path,
    global_ronin_override: bool | None = None,
) -> tuple[Tier, str | None]:
    """Single entry point.

    Order (per R2):
      1. Check hard limits — if triggered, return (HARD_STOP, None) immediately.
         Hard limits ALWAYS win, including over an existing approval, so an old
         approval can never bypass a newly added hard limit.
      2. If an `approved` entry matches in hitl_queue.json, consume it and return
         (AUTO, None). Closes the approval loop: human approves → next natural
         trigger finds it → executes.
      3. Otherwise resolve ronin mode, compute tier, enqueue if QUEUE/HITL.
    """
    repo_root = Path(repo_root)

    # Step 1: hard limits (R2: always first, even before approval consume)
    if _is_hard_limit(work_item, repo_root):
        _emit_decision(work_item, Tier.HARD_STOP, repo_root)
        return (Tier.HARD_STOP, None)

    # Step 2: consume any approval — returns queue_id so callers (TS reflex engine,
    # SENSEI) can drive `--complete` on the same queue item when the skill finishes.
    consumed_id = _consume_approval(work_item, repo_root)
    if consumed_id is not None:
        _emit_decision(work_item, Tier.AUTO, repo_root, queue_id=consumed_id, consumed=True)
        return (Tier.AUTO, consumed_id)

    # Step 3: resolve ronin + compute tier
    ronin = resolve_ronin_mode(work_item.pillar, repo_root, global_ronin_override)
    tier = compute_tier(work_item, ronin_mode=ronin)

    if tier == Tier.HARD_STOP:
        # Defense in depth: should already have been caught by _is_hard_limit
        _emit_decision(work_item, Tier.HARD_STOP, repo_root)
        return (Tier.HARD_STOP, None)

    if tier in (Tier.QUEUE, Tier.HITL):
        queue_id = enqueue_hitl(work_item, tier, repo_root)
        _emit_decision(work_item, tier, repo_root, queue_id=queue_id)
        return (tier, queue_id)

    # AUTO (plain) — intentionally not logged (high-frequency noise)
    return (Tier.AUTO, None)


__all__ = [
    "Tier",
    "BlastRadius",
    "WorkItem",
    "compute_tier",
    "decide",
    "enqueue_hitl",
    "mark_complete",
    "resolve_ronin_mode",
    "load_skill_metadata",
    "skill_to_work_item",
]
