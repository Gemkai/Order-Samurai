#!/usr/bin/env python
"""CLI bridge for the Bushido Engine.

Decision mode (default):
    python bin/bushido_check.py --skill simplify [--pillar arts]
        [--metric metric:arts:Simplify_Age] [--source reflex]
        [--backlog-id BACKLOG-42] [--stuck] [--consecutive 2]
        [--context "free-form text"]

    Stdout: {"tier": "queue", "queue_id": "hitl-a1b2c3d4",
             "ronin_mode": false, "blast_radius": "repo", "reversible": true}

    Exit:
        0  AUTO   — execute (genuinely AUTO, or an `approved` entry was consumed)
        1  QUEUE/HITL — suppressed, enqueued in state/hitl_queue.json
        2  HARD_STOP   — blocked (also: engine error on a SENSITIVE skill —
           fail-closed, JSON carries "fail_closed": true)
        3  Python error on a low-risk/unknown skill — caller fails open

Completion mode:
    python bin/bushido_check.py --complete <queue_id> [--failed]

    Exit: 0 if item was found and updated, 1 if not found, 3 on error.

Review mode (the human decision this queue exists for):
    python bin/bushido_check.py --approve <queue_id> [--reason "..."]
    python bin/bushido_check.py --reject <queue_id> --reason "..."
    python bin/bushido_check.py --expire <queue_id> --reason "..."

    Only acts on an item still `pending`; already-decided items are untouched.
    Exit: 0 if item was found and reviewed, 1 if not found/not pending, 3 on error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# Resolve repo root: env override > script-relative
_HERE = Path(__file__).resolve()
_DEFAULT_REPO = _HERE.parent.parent
REPO_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_DEFAULT_REPO)))

# Make agentica_core importable — it lives in the canonical Governance kernel
# (parents[2]), not in this repo. REPO_ROOT stays bound to Order Samurai for state.
sys.path.insert(0, str(REPO_ROOT))
_GOVERNANCE = _HERE.parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

try:
    from agentica_core.bushido_engine import (  # noqa: E402
        BlastRadius,
        ROLE_REQUESTED_CEILING,
        Tier,
        WorkItem,
        decide,
        mark_complete,
        resolve_role_binding,
        resolve_ronin_mode,
        review_hitl,
        skill_to_work_item,
    )
except Exception as e:  # noqa: BLE001
    sys.stderr.write(f"bushido_check: failed to import bushido_engine: {e}\n")
    sys.stderr.write(traceback.format_exc())
    sys.exit(3)


_SENSITIVE_BLAST = {"system", "irreversible"}
_SENSITIVE_TIERS = {"hitl", "hard_stop"}


def _is_sensitive_skill(skill_name: str, repo_root: Path) -> bool:
    """Classify a skill as sensitive by reading state/skill_tiers.json DIRECTLY.

    Engine-independent on purpose: the decision-error path that calls this is
    reached precisely because the engine raised, so it must not route back
    through the engine. A skill is sensitive when any high-risk attribute holds:
      - blast_radius in {system, irreversible}
      - reversible is explicitly False
      - approval_tier in {hitl, hard_stop}
    Unknown / untabled skills are NOT sensitive — preserve fail-open for the
    long tail of low-risk skills. Any read/parse error -> False (the engine
    error already yields exit 3; a missing table must not manufacture a
    hard-stop).
    """
    try:
        name = (skill_name or "").lstrip("/")
        path = Path(repo_root) / "state" / "skill_tiers.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        skills = data.get("skills", {})
        meta = skills.get(name) if isinstance(skills, dict) else None
        if not isinstance(meta, dict):
            return False
        if str(meta.get("blast_radius", "")).lower() in _SENSITIVE_BLAST:
            return True
        if meta.get("reversible") is False:
            return True
        if str(meta.get("approval_tier", "")).lower() in _SENSITIVE_TIERS:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bushido_check",
        description="Tier-decision bridge for the Bushido Engine.",
    )
    p.add_argument("--skill", help="Skill name (no leading slash). Required for decision mode.")
    p.add_argument("--pillar", default=None, help="Pillar slug (bow/sword/brush/arts) or empty.")
    p.add_argument("--metric", dest="metric_id", default=None,
                   help="Metric id (e.g. metric:arts:Simplify_Age). Optional.")
    p.add_argument("--source", default="reflex", choices=["reflex", "meditation", "manual", "cli"],
                   help="What triggered this decision.")
    p.add_argument("--backlog-id", dest="backlog_id", default=None,
                   help="Meditation backlog item id (used in the approval key).")
    p.add_argument("--stuck", action="store_true",
                   help="Mark this work item as already stuck (loop-breaker fired).")
    p.add_argument("--consecutive", dest="consecutive", type=int, default=0,
                   help="Consecutive no-improvement count.")
    p.add_argument("--context", default="", help="Free-form context for HITL reviewers.")
    p.add_argument("--command", default=None,
                   help="Override the auto-derived /skill command line.")
    p.add_argument("--complete", dest="complete", metavar="QUEUE_ID", default=None,
                   help="Completion mode: mark this queue item done. Use with --failed for failures.")
    p.add_argument("--failed", action="store_true",
                   help="With --complete: mark the item as failed instead of done.")
    p.add_argument("--approve", dest="approve", metavar="QUEUE_ID", default=None,
                   help="Review mode: approve a pending queue item.")
    p.add_argument("--reject", dest="reject", metavar="QUEUE_ID", default=None,
                   help="Review mode: reject a pending queue item. Requires --reason.")
    p.add_argument("--expire", dest="expire", metavar="QUEUE_ID", default=None,
                   help="Review mode: expire a pending queue item. Requires --reason.")
    p.add_argument("--reason", default="",
                   help="With --approve/--reject/--expire: the human's stated reason.")
    p.add_argument("--resolve-role-binding", dest="resolve_role_binding", nargs=2,
                   metavar=("ROLE", "CEILING"),
                   help="D1: resolve mode. Narrow ROLE's intrinsic requested blast_radius "
                        "to at most CEILING (a BlastRadius value). Prints "
                        "{\"role\":..,\"requested\":..,\"ceiling\":..,\"effective\":..,"
                        "\"narrowed\":bool} and exits 0, or exits 3 on an invalid CEILING.")
    p.add_argument("--ronin-override", choices=["true", "false"], default=None,
                   help="Force ronin mode on/off for this call (testing).")
    return p


def _decision_exit_code(tier: Tier) -> int:
    if tier == Tier.AUTO:
        return 0
    if tier in (Tier.QUEUE, Tier.HITL):
        return 1
    if tier == Tier.HARD_STOP:
        return 2
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── Review mode ───────────────────────────────────────────────────────────
    review = [(a, v) for a, v in (("approve", args.approve), ("reject", args.reject),
                                   ("expire", args.expire)) if v]
    if len(review) > 1:
        parser.error("only one of --approve/--reject/--expire may be given at a time")
    if review:
        action, queue_id = review[0]
        if action in ("reject", "expire") and not args.reason:
            parser.error(f"--reason is required with --{action}")
        try:
            ok = review_hitl(queue_id, REPO_ROOT, action, reason=args.reason)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"bushido_check: review_hitl failed: {e}\n")
            sys.stderr.write(traceback.format_exc())
            return 3
        print(json.dumps({"reviewed": ok, "queue_id": queue_id, "action": action}))
        return 0 if ok else 1

    # ── D1: role-binding ceiling-resolve mode ────────────────────────────────
    # Checked before --skill is required so a caller doing pure ceiling
    # arithmetic (sensei-orchestrator's role-bound spawn path) never needs a
    # skill name. Engine-independent failure handling: an invalid CEILING is a
    # caller bug (bad config), not a runtime decision error, so it exits 3
    # rather than routing through the fail-open/fail-closed skill logic below.
    if args.resolve_role_binding:
        role, ceiling_str = args.resolve_role_binding
        try:
            ceiling = BlastRadius(ceiling_str)
        except ValueError:
            sys.stderr.write(f"bushido_check: invalid --resolve-role-binding ceiling: {ceiling_str!r}\n")
            print(json.dumps({"effective": None, "error": f"invalid ceiling: {ceiling_str!r}"}))
            return 3
        requested = ROLE_REQUESTED_CEILING.get(role, BlastRadius.REPO)
        effective = resolve_role_binding(role, ceiling)
        print(json.dumps({
            "role": role,
            "requested": requested.value,
            "ceiling": ceiling.value,
            "effective": effective.value,
            "narrowed": effective != requested,
        }))
        return 0

    # ── Completion mode ──────────────────────────────────────────────────────
    if args.complete:
        try:
            ok = mark_complete(args.complete, REPO_ROOT, failed=bool(args.failed))
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"bushido_check: mark_complete failed: {e}\n")
            sys.stderr.write(traceback.format_exc())
            return 3
        print(json.dumps({"complete": ok, "queue_id": args.complete,
                          "status": "failed" if args.failed else "done"}))
        return 0 if ok else 1

    # ── Decision mode ────────────────────────────────────────────────────────
    if not args.skill:
        parser.error("--skill is required (unless --complete is given)")

    try:
        kwargs: dict = {}
        if args.metric_id:
            kwargs["metric_id"] = args.metric_id
        if args.pillar:
            kwargs["pillar"] = args.pillar
        if args.backlog_id:
            kwargs["backlog_id"] = args.backlog_id
        if args.consecutive:
            kwargs["consecutive_no_improvement"] = int(args.consecutive)
        if args.stuck:
            kwargs["stuck"] = True
        if args.context:
            kwargs["context"] = args.context
        if args.command:
            kwargs["command"] = args.command

        work_item = skill_to_work_item(
            skill_name=args.skill.lstrip("/"),
            source=args.source,
            repo_root=REPO_ROOT,
            **kwargs,
        )

        global_override: bool | None = None
        if args.ronin_override is not None:
            global_override = args.ronin_override == "true"

        tier, queue_id = decide(work_item, REPO_ROOT, global_ronin_override=global_override)
        ronin = resolve_ronin_mode(work_item.pillar, REPO_ROOT, global_override)

        out = {
            "tier": tier.value,
            "queue_id": queue_id,
            "ronin_mode": ronin,
            "blast_radius": (
                work_item.blast_radius.value
                if isinstance(work_item.blast_radius, BlastRadius)
                else str(work_item.blast_radius)
            ),
            "reversible": bool(work_item.reversible),
            "skill": work_item.skill,
            "pillar": work_item.pillar,
            "source": work_item.source,
        }
        print(json.dumps(out))
        return _decision_exit_code(tier)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"bushido_check: decision failed: {e}\n")
        sys.stderr.write(traceback.format_exc())
        # Fail CLOSED for sensitive skills: an engine error must never let a
        # high-blast/irreversible skill auto-fire. Callers already block on
        # exit 2 (HARD_STOP), so no caller change is needed. Low-risk/unknown
        # skills keep the historical fail-open behaviour (exit 3).
        if _is_sensitive_skill(args.skill, REPO_ROOT):
            try:
                print(json.dumps({
                    "tier": "hard_stop",
                    "queue_id": None,
                    "fail_closed": True,
                    "error": str(e),
                }))
            except Exception:
                pass
            return 2
        # Best-effort stdout so callers always get parseable JSON
        try:
            print(json.dumps({"tier": "error", "queue_id": None, "error": str(e)}))
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
