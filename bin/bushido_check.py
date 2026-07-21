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

# Make agentica_core importable
sys.path.insert(0, str(REPO_ROOT))

try:
    from agentica_core.bushido_engine import (  # noqa: E402
        BlastRadius,
        Tier,
        WorkItem,
        decide,
        mark_complete,
        resolve_ronin_mode,
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
    p.add_argument("--source", default="reflex", choices=["reflex", "dojo", "manual", "cli"],
                   help="What triggered this decision.")
    p.add_argument("--backlog-id", dest="backlog_id", default=None,
                   help="Dojo backlog item id (used in the approval key).")
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
