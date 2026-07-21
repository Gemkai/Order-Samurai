#!/usr/bin/env python
"""One-shot migration: state/backlog/needs_human_*.md  →  state/hitl_queue.json.

The legacy `needs_human_*.md` files are written by
Governance/agentica_core/skill_no_impact.py before Phase 3.2 swapped its
writer for bushido_check.py. This script ingests any still-present .md
tickets, converts them to pending HITL queue items, and removes the .md
files. Idempotent: running twice is harmless — already-migrated items are
deduped on (source, skill, pillar, metric_id, backlog_id).

Run once on Phase 3 deploy:
    python bin/migrate_hitl_md.py

Default behaviour is destructive (deletes the .md after migration). Pass
--dry-run to print what would be migrated without touching either file.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Anti-pattern #13: force UTF-8 stdout so any non-ASCII char in messages
# (or paths) can't crash this script with UnicodeEncodeError on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = Path(__file__).resolve()
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_REPO))

# agentica_core is the canonical Governance kernel (parents[2]), not this repo.
_GOVERNANCE = _HERE.parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from agentica_core.bushido_engine import (  # noqa: E402
    BlastRadius,
    Tier,
    WorkItem,
    enqueue_hitl,
    load_skill_metadata,
)


_KV_RE = re.compile(r"^- \*\*([^*]+)\*\*:\s*`([^`]+)`")


def _parse_md(path: Path) -> dict[str, str]:
    """Pull the `- **Key**: \`value\`` lines and the Recommended Intervention
    text out of one ticket file."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        m = _KV_RE.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()

    # Pull Recommended Intervention paragraph
    if "## Recommended Intervention" in text:
        rec = text.split("## Recommended Intervention", 1)[1]
        rec = rec.split("---", 1)[0].strip()
        fields["_recommendation"] = rec
    return fields


def migrate(repo_root: Path, dry_run: bool = False) -> tuple[int, int]:
    """Returns (migrated, skipped)."""
    backlog_dir = repo_root / "state" / "backlog"
    if not backlog_dir.exists():
        print(f"No backlog dir at {backlog_dir} — nothing to migrate.")
        return (0, 0)

    md_files = sorted(backlog_dir.glob("needs_human_*.md"))
    if not md_files:
        print("No needs_human_*.md tickets found.")
        return (0, 0)

    metadata = load_skill_metadata(repo_root)
    migrated = 0
    skipped = 0
    for md in md_files:
        try:
            fields = _parse_md(md)
        except OSError as e:
            print(f"  SKIP {md.name}: {e}")
            skipped += 1
            continue

        skill = (fields.get("Remediation Command", "/unknown")
                 .lstrip("/").split()[0])
        metric_id = fields.get("Metric ID", "")
        pillar = fields.get("Pillar") or None
        consecutive = int(fields.get("Consecutive Failed Runs", "0") or 0)
        context = fields.get("_recommendation") or fields.get("Failure Mode", "")

        meta = metadata.get(skill, {})
        blast_str = meta.get("blast_radius", "repo")
        try:
            blast = BlastRadius(blast_str)
        except ValueError:
            blast = BlastRadius.REPO
        reversible = bool(meta.get("reversible", True))

        wi = WorkItem(
            skill=skill,
            source="reflex",
            command=fields.get("Remediation Command", f"/{skill}"),
            blast_radius=blast,
            reversible=reversible,
            metric_id=metric_id or None,
            pillar=pillar,
            consecutive_no_improvement=consecutive,
            stuck=True,
            context=f"Migrated from {md.name}. " + context[:400],
        )

        if dry_run:
            print(f"  WOULD MIGRATE {md.name}  -> skill={skill} pillar={pillar} metric={metric_id}")
            migrated += 1
            continue

        qid = enqueue_hitl(wi, Tier.QUEUE, repo_root)
        print(f"  + {md.name}  ->  {qid}")
        try:
            md.unlink()
        except OSError as e:
            print(f"    (could not delete {md.name}: {e})")
        migrated += 1

    return (migrated, skipped)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Print without migrating.")
    p.add_argument("--repo-root", default=str(_REPO),
                   help="Order Samurai repo root (default: script-relative).")
    args = p.parse_args(argv)

    repo_root = Path(args.repo_root)
    migrated, skipped = migrate(repo_root, dry_run=args.dry_run)
    print(f"\nDone. {migrated} migrated, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
