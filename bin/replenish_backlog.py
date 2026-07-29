#!/usr/bin/env python3
"""
replenish_backlog.py — called by daemon when approved backlog is empty.

Reads METRICS.md, finds metric rows not yet in the backlog, scores them by
keyword density, and proposes the top 5 into PROPOSED_BACKLOG.json.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METRICS_MD = PROJECT_ROOT / "Research" / "METRICS.md"   # read-only source doc — worktree copy is fine
# State is canonical in the MAIN tree; meditation_overnight.sh exports MEDITATION_STATE_DIR when the
# cycle runs in a worktree so the proposals a later `bin/ronin promote` reads (from the main tree)
# actually land there, not in the disposable worktree. Falls back to script-relative for manual use.
_STATE_DIR = Path(os.environ["MEDITATION_STATE_DIR"]) if os.environ.get("MEDITATION_STATE_DIR") else PROJECT_ROOT / "state"
MEDITATION_STATE = _STATE_DIR / "MEDITATION_STATE.json"
PROPOSED_BACKLOG = _STATE_DIR / "PROPOSED_BACKLOG.json"

SCORE_KEYWORDS = {"security", "token", "cost", "error", "latency", "drift"}

PILLAR_KEYWORDS = {
    "bow": {"bow", "operational", "session", "tool", "latency", "error", "hook",
            "zombie", "daemon", "mechanism", "failure", "heal", "config"},
    "sword": {"sword", "security", "secret", "vulnerability", "guardrail",
              "permission", "boundary", "canary", "gate", "violation"},
    "brush": {"brush", "token", "cost", "mcp", "cli", "model", "orchestrat",
              "subagent", "architect", "skill", "chain", "revision", "cache"},
    "arts": {"arts", "vibe", "slop", "doc", "frustration", "rework",
             "simplify", "review", "lesson", "nudge", "visual", "acceptance"},
}

# Regex for 4-column table rows: | Metric | Measures | Source | Status |
ROW_RE = re.compile(r"^\|\s*\*{0,2}([^|*][^|]+?)\*{0,2}\s*\|([^|]+)\|([^|]+)\|([^|]+)\|")

# Fallback for 3-column table rows: | Metric | Signal source | Status |
# (used by the "Harness-derived expansion" section of METRICS.md). Only tried
# when ROW_RE fails to match, since ROW_RE's stricter 4-column shape would
# otherwise be shadowed by this looser one for ordinary rows.
ROW_RE_3COL = re.compile(r"^\|\s*\*{0,2}([^|*][^|]+?)\*{0,2}\s*\|([^|]+)\|([^|]+)\|\s*$")


def infer_pillar(text: str) -> str:
    lower = text.lower()
    scores = {pillar: 0 for pillar in PILLAR_KEYWORDS}
    for pillar, kws in PILLAR_KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                scores[pillar] += 1
    return max(scores, key=lambda p: scores[p])


def infer_kind(status: str) -> str:
    status = status.strip().upper()
    if "+SCOUT" in status:
        return "scout"
    if "+STREAM" in status:
        return "stream"
    if "+FIELD" in status:
        return "field"
    if "+SKILL" in status:
        return "skill"
    return "scout"


def score_row(title: str, measures: str) -> int:
    text = (title + " " + measures).lower()
    return sum(1 for kw in SCORE_KEYWORDS if kw in text)


def load_backlog_items() -> list:
    """Items already promoted into the MEDITATION_STATE backlog (titles + used ids)."""
    try:
        items = json.loads(MEDITATION_STATE.read_text(encoding="utf-8")).get("backlog", [])
        return [i for i in items if isinstance(i, dict)]
    except Exception:
        return []


def load_existing_titles() -> set:
    """Return lowercased title substrings already in MEDITATION_STATE backlog."""
    return {item.get("title", "").lower() for item in load_backlog_items()}


def load_proposed() -> dict:
    try:
        return json.loads(PROPOSED_BACKLOG.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": "", "note": "Run bin/ronin propose to push approved:true items to MEDITATION_STATE.json", "items": []}


def already_proposed(title: str, proposed_items: list) -> bool:
    lower = title.lower()
    return any(lower == item.get("title", "").lower() for item in proposed_items)


def next_auto_id(proposed_items: list) -> str:
    existing_nums = []
    for item in proposed_items:
        m = re.match(r"AUTO-(\d+)", item.get("id", ""))
        if m:
            existing_nums.append(int(m.group(1)))
    n = max(existing_nums, default=0) + 1
    return f"AUTO-{n:03d}"


def parse_candidates(existing_titles: set) -> list:
    text = METRICS_MD.read_text(encoding="utf-8")
    candidates = []
    current_pillar_hint = "bow"

    for line in text.splitlines():
        # Track section headers to use as pillar hint
        header = re.match(r"^#{1,3}\s+(.*)", line)
        if header:
            h = header.group(1).lower()
            for p in ("bow", "sword", "brush", "arts"):
                if p in h:
                    current_pillar_hint = p
                    break

        m = ROW_RE.match(line)
        if m:
            raw_title = m.group(1).strip()
            measures = m.group(2).strip()
            status = m.group(4).strip()
        else:
            m3 = ROW_RE_3COL.match(line)
            if not m3:
                continue
            raw_title = m3.group(1).strip()
            measures = m3.group(2).strip()
            status = m3.group(3).strip()

        # Skip rows that are already LIVE — they don't need backlog work.
        # Normalize markdown/whitespace so "**LIVE**", "LIVE (approx)", and
        # "**LIVE** (AUTO-005 ...)" all count, not just the bare "LIVE" string.
        if status.upper().strip("* `").startswith("LIVE"):
            continue

        # Skip header rows and separator rows
        if raw_title.lower() in ("metric", "metric name", "name"):
            continue
        if re.match(r"^[-:]+$", raw_title):
            continue

        # Skip if already in the meditation backlog (keyword match)
        title_lower = raw_title.lower()
        already_in_meditation = any(
            existing and (title_lower in existing or existing in title_lower)
            for existing in existing_titles
        )
        if already_in_meditation:
            continue

        pillar = infer_pillar(raw_title + " " + measures + " " + current_pillar_hint)
        kind = infer_kind(status)
        value = score_row(raw_title, measures)

        candidates.append({
            "title": raw_title,
            "measures": measures,
            "pillar": pillar,
            "kind": kind,
            "value": value,
        })

    # Deduplicate by title
    seen = set()
    unique = []
    for c in candidates:
        key = c["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


def build_items(candidates: list, proposed_items: list, auto_approve: bool,
                promoted_items: list | None = None) -> list:
    # Filter out already-proposed
    fresh = [c for c in candidates if not already_proposed(c["title"], proposed_items)]

    # Sort: higher value first, then alphabetical for stability
    fresh.sort(key=lambda c: (-c["value"], c["title"]))

    # `ronin promote` MOVES approved items out of PROPOSED_BACKLOG into
    # MEDITATION_STATE, so numbering from the proposals alone restarts the counter
    # after every promote and re-issues an id the backlog already holds (the live
    # state carries a duplicate AUTO-041 from exactly this path). Reserve both sets.
    reserved = list(proposed_items) + list(
        load_backlog_items() if promoted_items is None else promoted_items)

    new_items = []
    for c in fresh[:5]:
        item_id = next_auto_id(reserved + new_items)
        item = {
            "id": item_id,
            "pillar": c["pillar"],
            "kind": c["kind"],
            "title": c["title"],
            "value": c["value"],
            "effort": 2,
            "status": "proposed",
            "approved": auto_approve,
        }
        new_items.append(item)

    return new_items


def main():
    parser = argparse.ArgumentParser(
        description="Replenish PROPOSED_BACKLOG.json with top metric candidates from METRICS.md."
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Set approved=true on all proposed items (for tests).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed items without writing to disk.",
    )
    args = parser.parse_args()

    # Validate inputs
    if not METRICS_MD.exists():
        print(f"ERROR: METRICS.md not found at {METRICS_MD}", file=sys.stderr)
        sys.exit(1)
    if not MEDITATION_STATE.exists():
        print(f"ERROR: MEDITATION_STATE.json not found at {MEDITATION_STATE}", file=sys.stderr)
        sys.exit(1)

    existing_titles = load_existing_titles()
    proposed = load_proposed()
    existing_proposed = proposed.get("items", [])

    candidates = parse_candidates(existing_titles)
    new_items = build_items(candidates, existing_proposed, args.auto_approve)

    if not new_items:
        print("Proposed 0 items. Backlog is fully covered or no new candidates found.")
        return

    if args.dry_run:
        print(f"Dry run — would propose {len(new_items)} item(s):")
        for item in new_items:
            print(f"  [{item['id']}] ({item['pillar']}/{item['kind']}) {item['title']}  value={item['value']}")
        return

    proposed["items"] = existing_proposed + new_items
    proposed["generated_at"] = datetime.now(timezone.utc).isoformat()

    PROPOSED_BACKLOG.write_text(
        json.dumps(proposed, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Proposed {len(new_items)} items. Review: bin/ronin propose")
    for item in new_items:
        flag = " [auto-approved]" if item["approved"] else ""
        print(f"  [{item['id']}] ({item['pillar']}/{item['kind']}) {item['title']}  value={item['value']}{flag}")


if __name__ == "__main__":
    main()
