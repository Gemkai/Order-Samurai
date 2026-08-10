"""Bushido Engine — unified tier-decision module for Order Samurai.

Single source of truth that the TS Reflex Engine (via bin/bushido_check.py
subprocess) and the SENSEI meditation cycle both route through. Decides whether a
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

A tabled skill may name its tier explicitly via skill_tiers.json
`approval_tier`, overriding the matrix cell but never the hard-stop guards
above. This is an allowlist for the cases the 2-axis matrix cannot express —
today only `ronin-pillar`, whose repo-blast writes are the entire point of
ronin mode. Untabled skills carry no override.

Stdlib only; no external dependencies.
"""
from __future__ import annotations

import functools
import inspect
import json
import math
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .atomic import file_write_lock


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


# ── Phase D — role/authority ceiling resolution ────────────────────────────────
#
# D1 (docs/plans/2026-07-27-meta-harness-uplift.md:162): a package's role_binding
# names an `authority_ceiling` on this SAME BlastRadius scale — no new taxonomy.
# The scale is already an implicit narrow-to-wide ordering (state-only < repo <
# outside-repo < unrecoverable); this section makes that ordering explicit and
# exposes the one operation a binding needs: narrow a requested blast radius to
# at most its ceiling, never past it.
_BLAST_RANK: dict[BlastRadius, int] = {
    BlastRadius.CONFINED: 0,
    BlastRadius.REPO: 1,
    BlastRadius.SYSTEM: 2,
    BlastRadius.IRREVERSIBLE: 3,
}

# A role names the ceiling it intrinsically requests, before any binding narrows
# it further. New roles are added here, never invented as ad-hoc strings at a
# call site — bushido_engine stays the single authority model DRY calls for.
ROLE_REQUESTED_CEILING: dict[str, BlastRadius] = {
    "read-only-reviewer": BlastRadius.CONFINED,
    "writable-implementer": BlastRadius.REPO,
}


def resolve_ceiling(requested: BlastRadius, ceiling: BlastRadius) -> BlastRadius:
    """Narrow `requested` to at most `ceiling` on the BlastRadius scale.

    Pure, total order comparison — the tighter (lower-rank) of the two always
    wins. A role_binding's `authority_ceiling` can only ever narrow what a
    package asked for; it can never grant more than it declares. E.g.
    resolve_ceiling(REPO, CONFINED) -> CONFINED, resolve_ceiling(CONFINED, REPO)
    -> CONFINED (already narrower than the ceiling, so untouched).
    """
    return requested if _BLAST_RANK[requested] <= _BLAST_RANK[ceiling] else ceiling


def resolve_role_binding(role: str, authority_ceiling: BlastRadius) -> BlastRadius:
    """The entry point a `role_binding` entry (`{package@version, role,
    authority_ceiling}`) resolves through to get its EFFECTIVE blast radius.

    `role` looks up its intrinsic requested ceiling in ROLE_REQUESTED_CEILING;
    an unrecognized role defaults to requesting REPO — matching
    skill_to_work_item's existing "unknown -> REPO" default. That default is
    safe specifically because resolve_ceiling only ever narrows: a wrong REPO
    guess for an unknown role can still never be granted more than
    `authority_ceiling` allows.
    """
    requested = ROLE_REQUESTED_CEILING.get(role, BlastRadius.REPO)
    return resolve_ceiling(requested, authority_ceiling)


# ── Work item ─────────────────────────────────────────────────────────────────

@dataclass
class WorkItem:
    """Unified type that both a Reflex breach and a Meditation backlog item map to."""
    skill: str = ""
    source: str = ""              # "reflex" | "meditation" | other
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
    approval_tier: str | None = None   # explicit skill_tiers.json override; see compute_tier
    extra: dict[str, Any] = field(default_factory=dict)


# ── Core tier computation (pure, no I/O) ──────────────────────────────────────

def _parse_tier(value: Any) -> Tier | None:
    """Coerce a skill_tiers.json `approval_tier` string to a Tier, else None."""
    try:
        return Tier(str(value).strip().lower())
    except (ValueError, AttributeError):
        return None


def compute_tier(work_item: WorkItem, ronin_mode: bool = False) -> Tier:
    """Pure 2-axis matrix, with an explicit per-skill override. No I/O.

    Hard limits encoded into blast_radius/reversible take precedence over both
    ronin_mode and the override: an IRREVERSIBLE op never collapses to AUTO.

    `work_item.approval_tier` (from skill_tiers.json) is an ALLOWLIST: it names
    the tier for one tabled skill explicitly instead of deriving it from the
    2-axis matrix. It is honored only *after* the two hard-stop guards below, so
    it can never buy an irreversible or system-irreversible op an auto-fire; and
    `decide()` re-checks runtime hard limits (budget) before ever calling here.
    Untabled skills carry no override and keep the matrix default (QUEUE) — the
    override widens nothing on its own, it only records a deliberate exception.
    """
    blast = work_item.blast_radius
    reversible = bool(work_item.reversible)

    # Irreversible blast always hard-stops (git push, unreplicated delete, etc.)
    if blast == BlastRadius.IRREVERSIBLE:
        return Tier.HARD_STOP

    # Irreversible action on repo/system blast: too costly to auto-fire.
    if not reversible and blast in (BlastRadius.REPO, BlastRadius.SYSTEM):
        return Tier.HARD_STOP

    override = _parse_tier(work_item.approval_tier)
    if override is not None:
        tier = override
    elif reversible:
        tier = Tier.AUTO if blast == BlastRadius.CONFINED else Tier.QUEUE
    else:
        # Irreversible + confined (e.g. delete a state file in the queue) → HITL
        tier = Tier.HITL

    if ronin_mode and tier in (Tier.QUEUE, Tier.HITL):
        return Tier.AUTO

    return tier


# ── Hard limits (runtime state — budget, etc.) ────────────────────────────────

def _over_daily_budget(repo_root: Path) -> bool:
    """Read state/budget_ledger.json, failing closed on invalid control data.

    A missing file is a legitimate first-run state and uses the historical default
    (not over budget). Once the file exists, unreadable/malformed/non-finite values
    cannot disable the hard limit: they conservatively report over budget until an
    operator repairs the ledger. A valid different date still means a fresh day.
    """
    ledger_path = Path(repo_root) / "state" / "budget_ledger.json"
    try:
        d = json.loads(ledger_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except Exception:
        return True

    try:
        if not isinstance(d, dict):
            return True
        raw_date = d.get("date")
        if not isinstance(raw_date, str):
            return True
        ledger_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        today = datetime.now(timezone.utc).date()
        if ledger_date != today:
            return False
        spent = float(d.get("spent_usd", 0) or 0)
        # `or` must not collapse an explicit 0 (a budget freeze) into the default.
        raw_limit = d.get("daily_limit_usd")
        limit = 5.0 if raw_limit in (None, "") else float(raw_limit)
        if not math.isfinite(spent) or not math.isfinite(limit) or spent < 0 or limit < 0:
            return True
        return spent >= limit
    except Exception:
        return True


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

    Unknown skill → blast_radius=REPO, reversible=True, no override → QUEUE
    (safe). A tabled skill's `approval_tier` rides along as an explicit
    override; see compute_tier for the bound on what it can do.
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
    kwargs.setdefault("approval_tier", meta.get("approval_tier"))
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
      3. MEDITATION_STATE.json top-level "ronin_mode" ("ronin" -> True, else False)
      4. MEDITATION_STATE.json pillars[pillar].ronin_mode
      5. False
    """
    if global_override is not None:
        return bool(global_override)

    env_val = os.environ.get("BUSHIDO_RONIN_GLOBAL")
    if env_val is not None and env_val != "":
        return _is_ronin(env_val)

    try:
        state_path = Path(repo_root) / "state" / "MEDITATION_STATE.json"
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


# ── Standing approvals ("approve and don't ask again") ─────────────────────────
#
# ADOPT-003 (ratified 2026-08-09). A human can approve a `queue`-tier item AND
# record that its whole class — the same (source, skill, pillar, metric_id,
# backlog_id) key `_approval_key` already uses — should stop re-prompting.
# Deliberately a SEPARATE file from hitl_queue.json: a standing approval is a
# durable PREFERENCE (like a watermark), not a QUEUE ITEM with its own
# pending/approved/rejected lifecycle, and the two files already have four
# concurrent writers between them — no reason to serialize preference grants
# behind the same lock queue mutations use.
#
# Safety scoping, enforced at the ONLY two places that matter (grant and
# consult), not by convention: standing approval is refused for anything but
# `Tier.QUEUE` (reversible-but-wider-blast items). HITL tier — irreversible +
# confined — and HARD_STOP never have a path to this file, matching the
# compute_tier matrix's own reversible/irreversible boundary. A grant call for
# a non-queue tier is a no-op (returns False, warns) rather than an exception:
# the one-off approve it rode in on must still succeed.

_STANDING_APPROVALS_FILE = "hitl_standing_approvals.json"


def _standing_approvals_path(repo_root: Path) -> Path:
    return Path(repo_root) / "state" / _STANDING_APPROVALS_FILE


def _load_standing_approvals(repo_root: Path) -> dict:
    path = _standing_approvals_path(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("hitl_standing_approvals.json root must be an object")
        data.setdefault("schema_version", 1)
        data.setdefault("entries", [])
        return data
    except (OSError, ValueError):
        if path.exists():
            try:
                path.replace(path.with_name("hitl_standing_approvals.corrupt.json"))
            except OSError:
                pass
        now = datetime.now(timezone.utc).isoformat()
        return {"schema_version": 1, "created_at": now, "updated_at": now, "entries": []}


def _key_to_list(key: tuple) -> list:
    return list(key)


def _entry_key(entry: dict) -> tuple:
    raw = entry.get("key")
    if isinstance(raw, list):
        return tuple(raw)
    return ("", "", "", "", "")


def grant_standing_approval(
    key: tuple, repo_root: Path, source_queue_id: str, tier_at_grant: str, reason: str = "",
) -> bool:
    """Record a standing "don't ask again" for `key`. Refuses (returns False,
    no write, no exception) unless `tier_at_grant == "queue"` — the whole
    point of the tier matrix is that HITL/HARD_STOP items need a human's eyes
    EVERY time; this is the gate that keeps a standing approval from ever
    reaching them. Idempotent: re-granting an existing key refreshes it in
    place rather than duplicating.
    """
    if tier_at_grant != Tier.QUEUE.value:
        _warn(
            f"bushido_engine: refusing standing approval for {key!r} — "
            f"tier_at_grant={tier_at_grant!r}, only {Tier.QUEUE.value!r} is eligible"
        )
        return False
    repo_root = Path(repo_root)
    path = _standing_approvals_path(repo_root)
    with file_write_lock(path):
        data = _load_standing_approvals(repo_root)
        now = datetime.now(timezone.utc).isoformat()
        for entry in data["entries"]:
            if _entry_key(entry) == key:
                entry["granted_at"] = now
                entry["granted_via_queue_id"] = source_queue_id
                entry["reason"] = reason
                break
        else:
            data["entries"].append({
                "key": _key_to_list(key),
                "granted_at": now,
                "granted_via_queue_id": source_queue_id,
                "tier_at_grant": tier_at_grant,
                "reason": reason,
            })
        data["updated_at"] = now
        _atomic_write_json(path, data)
    return True


def revoke_standing_approval(key: tuple, repo_root: Path) -> bool:
    """Remove a standing approval if present. Returns whether anything was removed."""
    repo_root = Path(repo_root)
    path = _standing_approvals_path(repo_root)
    with file_write_lock(path):
        data = _load_standing_approvals(repo_root)
        before = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if _entry_key(e) != key]
        if len(data["entries"]) == before:
            return False
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(path, data)
    return True


def _has_standing_approval(key: tuple, repo_root: Path) -> bool:
    data = _load_standing_approvals(Path(repo_root))
    return any(_entry_key(e) == key for e in data["entries"])


def list_standing_approvals(repo_root: Path) -> list[dict]:
    """Read-only, for the CLI/audit surface — a standing preference must stay
    legible, not a black box a human granted once and can never see again."""
    return list(_load_standing_approvals(Path(repo_root))["entries"])


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


def _under_queue_lock(fn):
    """Serialise one load-mutate-write of hitl_queue.json across processes.

    Every function below loads the queue, mutates it and writes it back. `_atomic_write_json`
    makes each write indivisible, which stops a torn READ; it does nothing about a lost UPDATE.
    Four processes touch this file — the reflex engine (via route_work_item), the sensei cycle's
    self-harness delivery, its rival-verdict recorder, and the held-out rotation check — so two
    of them loading the same bytes and writing back silently keeps only the second, and what is
    dropped is a human's pending approval.

    A decorator rather than a `with` inside each body: the lock has to cover the load as well as
    the write, and wrapping whole bodies would reindent four functions of live routing code for
    no behavioural gain. NOT re-entrant (flock blocks a second open() from the same process), so
    these four must never nest — route_work_item calls _consume_approval and enqueue_hitl in
    sequence, never one inside the other.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        repo_root = bound.arguments["repo_root"]
        with file_write_lock(Path(repo_root) / "state" / "hitl_queue.json"):
            return fn(*args, **kwargs)

    return wrapper


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
        # An absent queue is a first run — a fresh one is correct. A queue that
        # EXISTS but won't parse means the read failed, not that there were no
        # approvals; enqueue_hitl rewrites this file in full, so handing it a
        # fresh queue here silently destroys every pending/approved item. Move
        # the unreadable bytes aside first so the approvals stay recoverable.
        if path.exists():
            try:
                path.replace(path.with_name("hitl_queue.corrupt.json"))
            except OSError:
                pass
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": 1,
            "created_at": now,
            "updated_at": now,
            "items": [],
        }


@_under_queue_lock
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


@_under_queue_lock
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


@_under_queue_lock
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


@_under_queue_lock
def reconcile_stale_executing(
    repo_root: Path,
    *,
    max_age_hours: float = 2.0,
    now: datetime | None = None,
) -> int:
    """Fail expired execution leases so crashes cannot strand approvals forever.

    ``executing`` is a lease, not a terminal state. The TypeScript engine marks
    completion in its terminal callback, but a process crash/restart can skip
    that callback. Reconciliation is idempotent and deliberately marks stale
    rows ``failed`` rather than re-queueing them: replaying an unknown partially
    executed repo mutation would be unsafe without fresh human review.
    """
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)

    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    data = _load_queue(repo_root)
    items = data.get("items", [])
    if not isinstance(items, list):
        return 0

    reconciled = 0
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "executing":
            continue
        raw = item.get("executing_at")
        started: datetime | None = None
        if isinstance(raw, str):
            try:
                started = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except ValueError:
                started = None
        if started is not None and started > cutoff:
            continue
        item["status"] = "failed"
        item["completed_at"] = now.isoformat()
        item["failure_reason"] = "execution_lease_expired"
        reconciled += 1

    if reconciled:
        data["updated_at"] = now.isoformat()
        _atomic_write_json(queue_path, data)
    return reconciled


# ── Shared queue helpers (timestamps, operator-visible logging) ───────────────

def _parse_ts(raw: Any) -> datetime | None:
    """Parse an ISO-8601 queue timestamp to an aware datetime, else None."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _warn(message: str) -> None:
    """Operator-visible warning on stderr — never swallowed, never fatal.

    stdout belongs to bin/bushido_check.py's JSON contract, so this must not
    touch it; stderr is what the TS engine and the scheduled runners capture.
    """
    try:
        sys.stderr.write(message + "\n")
    except Exception:  # noqa: BLE001
        pass


def _emit_queue_event(
    event: str, item: dict, repo_root: Path, *, outcome: str, detail: str,
) -> None:
    """Audit line for a queue lifecycle event, mirroring `_emit_review`'s stream
    and never-raise contract — a broken audit write must not break the queue."""
    try:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "outcome": outcome,
            "queue_id": item.get("id"),
            "skill": item.get("skill"),
            "detail": detail,
        }
        if item.get("pillar"):
            payload["pillar"] = item["pillar"]
        if item.get("metric_id"):
            payload["metric_id"] = item["metric_id"]
        path = Path(repo_root) / "state" / "autonomic_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:  # noqa: BLE001
        pass


# ── Approval TTL — re-surface approvals that are rotting ──────────────────────

# Default TTL before an untouched `approved` item is re-surfaced. Overridable
# via BUSHIDO_APPROVAL_TTL_HOURS (read by _approval_ttl_hours below).
_APPROVAL_TTL_DEFAULT_HOURS = 24.0


def _approval_ttl_hours() -> float:
    """BUSHIDO_APPROVAL_TTL_HOURS, falling back to the 24h default.

    Malformed / non-positive values fall back rather than disabling the check —
    a broken dial must not silently turn re-notification off.
    """
    try:
        hours = float(os.environ.get("BUSHIDO_APPROVAL_TTL_HOURS", ""))
    except ValueError:
        return _APPROVAL_TTL_DEFAULT_HOURS
    return hours if hours > 0 else _APPROVAL_TTL_DEFAULT_HOURS


@_under_queue_lock
def reconcile_stale_approved(
    repo_root: Path,
    *,
    max_age_hours: float | None = None,
    now: datetime | None = None,
) -> int:
    """Re-surface approvals that have sat in `approved` past their TTL.

    The failure this exists for: before push-on-approve, an approval only took
    effect if the SAME reflex re-fired while still eligible, so grants rotted
    silently (hitl-c998eca2, approved 2026-08-02, never executed). With the push
    path off — or after a push that could not reach the API — an item can still
    come to rest in `approved`, and nothing surfaced it: hitl_alerts.py reports
    `pending` and recently-`expired` only.

    Deliberately NON-DESTRUCTIVE, unlike reconcile_stale_executing: the item
    keeps `approved` status and stays consumable by `_consume_approval`. All this
    does is emit a `hitl_approval_stale` audit event and stamp the item so the
    reminder repeats at most once per TTL window instead of on every check.
    Returns the number of items re-surfaced by this call.
    """
    ttl = _approval_ttl_hours() if max_age_hours is None else float(max_age_hours)
    if ttl <= 0:
        raise ValueError("max_age_hours must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(hours=ttl)

    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    data = _load_queue(repo_root)
    items = data.get("items", [])
    if not isinstance(items, list):
        return 0

    resurfaced = 0
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "approved":
            continue
        approved_at = _parse_ts(item.get("approved_at"))
        # An approved row with no parseable timestamp is exactly the rot case —
        # treat it as stale rather than letting a bad field hide it forever.
        if approved_at is not None and approved_at > cutoff:
            continue
        last = _parse_ts(item.get("approval_renotified_at"))
        if last is not None and last > cutoff:
            continue  # already re-surfaced within this TTL window
        item["approval_renotified_at"] = now.isoformat()
        item["approval_renotify_count"] = int(item.get("approval_renotify_count") or 0) + 1
        _emit_queue_event(
            "hitl_approval_stale", item, repo_root,
            outcome="stale",
            detail=(
                f"approved {item.get('approved_at')} and still unexecuted after "
                f"{ttl}h — approval is not being consumed"
            ),
        )
        _warn(
            f"bushido_engine: approval {item.get('id')} ({item.get('skill') or '?'}) "
            f"has been approved-but-unexecuted for over {ttl}h"
        )
        resurfaced += 1

    if resurfaced:
        data["updated_at"] = now.isoformat()
        _atomic_write_json(queue_path, data)
    return resurfaced


# ── Push-on-approve transport ─────────────────────────────────────────────────
#
# A human approval used to be PULL-only: `_consume_approval` fired it if — and
# only if — the same reflex re-fired while still eligible, through cooldowns
# tripled by the efficacy penalty, recovered-metric suppression and REFUTED
# suppression. 145 of ~150 code-modifying attempts since 2026-07-21 queued;
# 4 approvals were ever granted and 3 consumed, one after 3.5 days.
#
# Push closes that loop by POSTing the EXISTING manual-run route (server.ts
# /api/reflex/exec -> spawnExec -> ReflexEngine.runManual). It is a transport,
# not a second execution path: the run still goes through the engine's staging
# worktree, maker-checker audit, pytest gate and propose-only handling, and
# AUTO_APPLY stays default-off. Only WHEN an approved run starts changes.

_PUSH_FALSEY = {"0", "false", "no", "off"}

# A pushed run git-applies inside the engine's staging pipeline, so the
# transport must never leave this machine: a non-loopback base URL is refused
# outright rather than silently dialled.
_PUSH_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_PUSH_DEFAULT_BASE = "http://127.0.0.1:3001"
_PUSH_ROUTE = "/api/reflex/exec"
_PUSH_DEFAULT_TIMEOUT_S = 5.0


def _push_on_approve_enabled() -> bool:
    """BUSHIDO_PUSH_ON_APPROVE — default ON (the ratified behaviour).

    Set to 0/false/no/off to restore the historical pull-only semantics exactly:
    `review_hitl` leaves the item `approved`, nothing is POSTed, and the approval
    is consumed only by `_consume_approval` on a later re-fire.
    """
    raw = os.environ.get("BUSHIDO_PUSH_ON_APPROVE")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in _PUSH_FALSEY


def _push_endpoint() -> str | None:
    """Absolute URL of the manual-run route, or None when misconfigured.

    Base URL comes from BUSHIDO_PUSH_API_BASE (default http://127.0.0.1:3001).
    Anything that is not plain http on a loopback host returns None, which the
    caller treats as a refused push (approval reverts to `approved`).
    """
    base = (os.environ.get("BUSHIDO_PUSH_API_BASE") or "").strip() or _PUSH_DEFAULT_BASE
    try:
        parts = urllib.parse.urlsplit(base)
    except ValueError:
        return None
    if parts.scheme != "http" or (parts.hostname or "").lower() not in _PUSH_LOOPBACK_HOSTS:
        return None
    return f"{parts.scheme}://{parts.netloc}{_PUSH_ROUTE}"


def _push_timeout_s() -> float:
    """BUSHIDO_PUSH_TIMEOUT_S — explicit, bounded, never unset."""
    try:
        timeout = float(os.environ.get("BUSHIDO_PUSH_TIMEOUT_S", ""))
    except ValueError:
        return _PUSH_DEFAULT_TIMEOUT_S
    return timeout if 0 < timeout <= 60 else _PUSH_DEFAULT_TIMEOUT_S


def _post_manual_run(command: str, endpoint: str, timeout_s: float) -> tuple[str, str]:
    """POST `command` to the manual-run route. Never raises. Returns (outcome, detail).

    The three outcomes exist to keep the approval at-most-once:
      "started"       — 2xx. The engine accepted the run; the claim settles.
      "refused"       — the run provably did NOT start (4xx from the route, or the
                        endpoint was unreachable/misconfigured, i.e. nothing was
                        ever delivered). Safe to hand the approval back to the
                        pull path.
      "indeterminate" — the request MAY have been delivered (socket timeout, 5xx).
                        Reverting here could double-fire, so the item stays on the
                        execution lease and `reconcile_stale_executing` owns it.
    """
    body = json.dumps({"command": command}).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        # `timeout` is the socket timeout urllib applies to the connect AND to
        # every read — this call can never block indefinitely.
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = int(getattr(response, "status", None) or response.getcode())
            if 200 <= status < 300:
                return ("started", f"HTTP {status}")
            return ("indeterminate", f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            return ("refused", f"HTTP {exc.code}")
        return ("indeterminate", f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return ("indeterminate", f"timeout after {timeout_s}s")
        return ("refused", f"unreachable: {exc.reason}")
    except (TimeoutError, socket.timeout):
        return ("indeterminate", f"timeout after {timeout_s}s")
    except Exception as exc:  # noqa: BLE001
        return ("indeterminate", f"{type(exc).__name__}: {exc}")


@_under_queue_lock
def _settle_push_claim(queue_id: str, repo_root: Path, claimed_at: str) -> bool:
    """A started push: promote the claim `executing` -> `dispatched` (terminal).

    `dispatched` rather than `done`: we know the run STARTED, not that it
    succeeded — its outcome is graded in exec_log.jsonl like every other manual
    run, and runManual carries no queue id back, so nothing will call
    `mark_complete` for it. Leaving it `executing` instead would let the 2h
    execution lease mislabel every pushed approval `failed`.

    Only settles a claim this call still owns (`push_claimed_at` unchanged), so a
    concurrent transition is never clobbered.
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
        if not isinstance(item, dict) or item.get("id") != queue_id:
            continue
        if item.get("status") != "executing" or item.get("push_claimed_at") != claimed_at:
            return False
        item["status"] = "dispatched"
        item["dispatched_at"] = now
        data["updated_at"] = now
        _atomic_write_json(queue_path, data)
        return True
    return False


@_under_queue_lock
def _release_push_claim(queue_id: str, repo_root: Path, claimed_at: str, detail: str) -> bool:
    """A refused push: hand the approval back to the legacy pull path.

    Restores `approved` (clearing the lease fields) so `_consume_approval` can
    still fire it on a later re-fire — a failed POST must never cost a human's
    approval. Same ownership guard as `_settle_push_claim`.
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
        if not isinstance(item, dict) or item.get("id") != queue_id:
            continue
        if item.get("status") != "executing" or item.get("push_claimed_at") != claimed_at:
            return False
        item["status"] = "approved"
        item["executing_at"] = None
        item["push_claimed_at"] = None
        item["push_failed_at"] = now
        item["push_failure_reason"] = detail
        data["updated_at"] = now
        _atomic_write_json(queue_path, data)
        return True
    return False


_REVIEW_ACTIONS = {"approve", "approve_always", "reject", "expire"}


def review_hitl(queue_id: str, repo_root: Path, action: str, reason: str = "") -> bool:
    """Transition a `pending` HITL item to `approved`, `rejected`, or `expired`.

    The state-transition writer the queue never had (audit finding: `pending`
    could only ever be entered, never left, other than via `_consume_approval`
    finding a pre-existing `approved` row — which nothing wrote). Only acts on
    items still `pending`; an already-approved/executing/done/rejected/expired
    item is untouched (returns False) so this cannot silently re-decide a
    settled item. Every call is audit-logged to autonomic_events.jsonl.

    `action="approve_always"` (ADOPT-003) does everything `"approve"` does —
    same push-on-approve behavior below — AND additionally records a standing
    approval for this item's (source, skill, pillar, metric_id, backlog_id)
    class, so a future matching item skips the queue entirely (see `decide()`).
    The grant is refused, silently to the caller (this call still returns True
    — the one-off approve is unaffected), for anything but `queue`-tier items;
    `grant_standing_approval` is the enforcement point.

    On `approve`/`approve_always`, with BUSHIDO_PUSH_ON_APPROVE on (the
    default), the approval is also pushed straight to the existing manual-run
    route instead of waiting for the same reflex to re-fire. AT-MOST-ONCE
    ordering, which is the whole game:

      1. Under the queue lock, the item goes `pending` -> `executing` with a
         `push_claimed_at` stamp — the claim is taken BEFORE any HTTP call, and
         `_consume_approval` only ever matches `approved`, so the pull path is
         locked out from the instant the push path owns the item.
      2. The POST happens OUTSIDE the lock (an HTTP call must not hold a
         cross-process flock) and can only move the item forward: `dispatched`
         on a started run, back to `approved` on a provably-refused one.
      3. An indeterminate result (timeout, 5xx — the request may have landed) is
         NOT reverted; the item stays on the execution lease that
         `reconcile_stale_executing` already owns. Losing an approval to a lease
         is recoverable and visible; double-firing a repo mutation is not.

    With the switch off, none of that runs and the semantics are byte-identical
    to the pull-only behaviour: the item comes to rest in `approved`.
    """
    if action not in _REVIEW_ACTIONS:
        raise ValueError(f"action must be one of {sorted(_REVIEW_ACTIONS)}, got {action!r}")

    repo_root = Path(repo_root)
    grant_standing = action == "approve_always"
    push = action in ("approve", "approve_always") and _push_on_approve_enabled()
    reviewed, claim, approved_snapshot = _review_hitl_locked(queue_id, repo_root, action, reason, push)

    if grant_standing and reviewed and approved_snapshot is not None:
        grant_standing_approval(
            _item_key(approved_snapshot), repo_root,
            source_queue_id=queue_id,
            tier_at_grant=str(approved_snapshot.get("tier_assigned") or ""),
            reason=reason,
        )

    if not reviewed or claim is None:
        return reviewed

    endpoint = _push_endpoint()
    if endpoint is None:
        outcome, detail = ("refused", "BUSHIDO_PUSH_API_BASE is not an http loopback URL")
    else:
        outcome, detail = _post_manual_run(claim["command"], endpoint, _push_timeout_s())

    _emit_queue_event("hitl_push", claim["item"], repo_root, outcome=outcome, detail=detail)
    if outcome == "started":
        _settle_push_claim(queue_id, repo_root, claim["claimed_at"])
    elif outcome == "refused":
        _release_push_claim(queue_id, repo_root, claim["claimed_at"], detail)
        _warn(
            f"bushido_engine: push-on-approve refused for {queue_id} ({detail}) — "
            f"approval returned to `approved` for the pull path"
        )
    else:
        _warn(
            f"bushido_engine: push-on-approve indeterminate for {queue_id} ({detail}) — "
            f"item left on the execution lease; it will not be re-fired"
        )
    return True


@_under_queue_lock
def _review_hitl_locked(
    queue_id: str, repo_root: Path, action: str, reason: str, push: bool,
) -> tuple[bool, dict | None, dict | None]:
    """The locked half of `review_hitl`: decide the item and, when pushing, claim it.

    Returns (reviewed, claim, approved_snapshot). `claim` is non-None only when
    this call moved a pending item to a push-owned `executing` lease and the
    caller must now POST. `approved_snapshot` is the item as it stood right
    after the `approved` transition (before any push-claim mutation), captured
    for both "approve" and "approve_always" — `review_hitl` uses it to grant a
    standing approval outside this lock, keyed on the item's OWN
    tier_assigned/source/skill/pillar/metric_id/backlog_id, never on the
    caller's say-so.
    """
    queue_path = Path(repo_root) / "state" / "hitl_queue.json"
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (False, None, None)

    items = data.get("items", [])
    if not isinstance(items, list):
        return (False, None, None)

    now = datetime.now(timezone.utc).isoformat()
    claim: dict | None = None
    approved_snapshot: dict | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("id") != queue_id or item.get("status") != "pending":
            continue
        if action in ("approve", "approve_always"):
            item["status"] = "approved"
            item["approved_at"] = now
            approved_snapshot = dict(item)
            command = item.get("command")
            # Only claim what the manual-run route would actually accept — a
            # missing/non-/skill command would just round-trip to a 400 and back.
            if push and isinstance(command, str) and command.strip().startswith("/"):
                item["status"] = "executing"
                item["executing_at"] = now
                item["push_claimed_at"] = now
                claim = {"claimed_at": now, "command": command.strip(), "item": dict(item)}
        elif action == "reject":
            item["status"] = "rejected"
            item["rejected_at"] = now
            item["rejected_reason"] = reason
        else:  # expire
            item["status"] = "expired"
            item["expired_at"] = now
            item["expired_reason"] = reason
        data["updated_at"] = now
        _atomic_write_json(queue_path, data)
        _emit_review(item, action, reason, repo_root)
        return (True, claim, approved_snapshot)
    return (False, None, None)


def _emit_review(item: dict, action: str, reason: str, repo_root: Path) -> None:
    """Audit line for every review action, mirroring `_emit_decision`'s stream
    and never-raise contract — a broken audit write must not break the review."""
    try:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "hitl_review",
            "action": action,
            "queue_id": item.get("id"),
            "skill": item.get("skill"),
            "detail": f"{item.get('skill') or '?'} -> {action}" + (f": {reason}" if reason else ""),
        }
        if item.get("pillar"):
            event["pillar"] = item["pillar"]
        if item.get("metric_id"):
            event["metric_id"] = item["metric_id"]
        path = Path(repo_root) / "state" / "autonomic_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except Exception:
        pass


# ── Decision audit log ────────────────────────────────────────────────────────

def _emit_decision(
    work_item: WorkItem,
    tier: Tier,
    repo_root: Path,
    queue_id: str | None = None,
    consumed: bool = False,
    standing: bool = False,
) -> None:
    """Best-effort audit line for every NON-plain-AUTO decision.

    Appends one JSON object to state/autonomic_events.jsonl — the repo-local
    stream the kill-chain scout also appends to directly (NOT the gated
    Governance emitter, which has a closed event-type allow-list). Plain AUTO is
    intentionally not logged to avoid high-frequency noise; an AUTO that came
    from consuming an approval or matching a standing approval IS logged (a
    human decision drove it, even if not in this exact moment). Never raises —
    audit logging must not break a decision.
    """
    try:
        tier_val = tier.value if isinstance(tier, Tier) else str(tier)
        detail = f"{work_item.skill or '?'} -> {tier_val}" + (
            " (approval consumed)" if consumed else
            " (standing approval — don't ask again)" if standing else ""
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

    Order (per R2, extended by ADOPT-003 step 3):
      1. Check hard limits — if triggered, return (HARD_STOP, None) immediately.
         Hard limits ALWAYS win, including over an existing approval, so an old
         approval can never bypass a newly added hard limit.
      2. If an `approved` entry matches in hitl_queue.json, consume it and return
         (AUTO, None). Closes the approval loop: human approves → next natural
         trigger finds it → executes.
      3. Resolve ronin mode, compute tier. If it lands on `queue` AND a standing
         approval matches this item's class, return (AUTO, None) WITHOUT ever
         touching hitl_queue.json — no pending entry, no digest email, nothing
         to re-approve. Structurally unreachable for `hitl`/`hard_stop` tiers:
         the check is gated on `tier == Tier.QUEUE` before it ever runs, the
         same boundary compute_tier itself draws between reversible-wide-blast
         and irreversible work.
      4. Otherwise enqueue if QUEUE/HITL, as before.
    """
    repo_root = Path(repo_root)

    # Repair crash-stranded executions before looking for a reusable approval.
    # Environment ownership makes the lease tunable without changing policy
    # code; malformed values fall back to the conservative two-hour default.
    try:
        lease_hours = float(os.environ.get("HITL_EXECUTION_LEASE_HOURS", "2"))
        if lease_hours <= 0:
            raise ValueError
    except ValueError:
        lease_hours = 2.0
    reconcile_stale_executing(repo_root, max_age_hours=lease_hours)
    # Same reconcile pass, other direction: an approval that has come to rest in
    # `approved` past its TTL gets re-surfaced (audit event + stderr) instead of
    # rotting invisibly. Non-destructive — the approval stays consumable below.
    reconcile_stale_approved(repo_root)

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

    # ADOPT-003: a standing "don't ask again" only ever applies to queue-tier
    # work — never hitl (irreversible+confined always needs a human) or
    # hard_stop (already returned above). No queue item is created at all.
    if tier == Tier.QUEUE and _has_standing_approval(_approval_key(work_item), repo_root):
        _emit_decision(work_item, Tier.AUTO, repo_root, standing=True)
        return (Tier.AUTO, None)

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
    "reconcile_stale_approved",
    "reconcile_stale_executing",
    "review_hitl",
    "resolve_ronin_mode",
    "load_skill_metadata",
    "skill_to_work_item",
    "resolve_ceiling",
    "resolve_role_binding",
    "ROLE_REQUESTED_CEILING",
    "grant_standing_approval",
    "revoke_standing_approval",
    "list_standing_approvals",
]
