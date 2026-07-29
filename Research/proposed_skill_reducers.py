#!/usr/bin/env python3
"""PROPOSED reducers — NOT wired into the live registry yet.

These two metrics are catalogued as PROPOSED in Research/METRICS.md (2026-07-06,
from the skill-library audit). They read sources that already exist, so they are
`source-ready`. They stay HERE until they clear the intake:

    METRICS.md  ->  replenish_backlog.py  ->  PROPOSED_BACKLOG.json (approve)
    ->  bin/ronin propose  ->  MEDITATION_STATE  ->  wire into the LIVE registry

On approval, move the two functions into the Governance aggregate.py copy (per the
kernel-drift rule: dashboard reducers live in the Governance copy, not the frozen
repo-local kernel) and append the REGISTRY entries at the bottom of this file.

Reducer contract (from ronin_metrics.py):
    reducer(records: list[dict], repo_root: Path) -> float | int | str

Both reducers below read from ~/.claude (the user's Claude home), like the existing
verifier-backed reducers that read ~/.claude/data — so they ignore `records` and
`repo_root` and mark ARG001.

Run this file directly to validate against the live sources:
    python3 proposed_skill_reducers.py
"""
from __future__ import annotations

import re
from pathlib import Path

CLAUDE_HOME = Path.home() / ".claude"
CHAIN_MAP = CLAUDE_HOME / "data" / "skill_chain_map.md"
ACTIVE_SKILLS_DIR = CLAUDE_HOME / "skills"


# ---------------------------------------------------------------------------
# arts / brush — Skill_Dead_Ref_Count
# Source: ~/.claude/data/skill_chain_map.md (skill_chain_map.py already tags every
# cross-ref by namespace; DEAD = resolves to nothing, RETIRED = lives in _retired/).
# A rising count means the skill graph has dangling references (e.g. a retired skill
# still pointed at, or an @ref to a nonexistent skill). Target: 0.
# ---------------------------------------------------------------------------
def _count_skill_dead_refs(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    try:
        text = CHAIN_MAP.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return -1  # -1 = source missing (chain map not generated); distinct from 0
    # Refs are tagged inline as `name·DEAD` / `name·RETIRED` (see chain-map legend).
    return len(re.findall(r"·DEAD\b", text)) + len(re.findall(r"·RETIRED\b", text))


# ---------------------------------------------------------------------------
# brush — Skill_Selector_Token_Weight
# Source: sum of the `description:` frontmatter across ~/.claude/skills/*/SKILL.md.
# Skill descriptions are injected into the skill-selection context every session;
# this is the per-session selector token cost. Retiring/trimming skills lowers it.
# Returns an approximate token count (chars / 4).
# ---------------------------------------------------------------------------
def _extract_description(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("---", 3)
    if end == -1:
        return ""
    fm = text[3:end]
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("description:"):
            _, _, v = line.partition(":")
            v = v.strip()
            if v in (">", "|", ">-", "|-", ""):
                # block scalar: collect indented continuation lines
                parts: list[str] = []
                for cont in lines[i + 1:]:
                    if cont and not cont[0].isspace():
                        break
                    parts.append(cont.strip())
                return " ".join(p for p in parts if p)
            return v.strip('"').strip("'")
    return ""


def _skill_selector_token_weight(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    if not ACTIVE_SKILLS_DIR.is_dir():
        return -1
    total_chars = 0
    for skill_dir in ACTIVE_SKILLS_DIR.iterdir():
        if not skill_dir.is_dir() or skill_dir.name == "_retired":
            continue
        total_chars += len(_extract_description(skill_dir / "SKILL.md"))
    return total_chars // 4  # ~4 chars per token


# ---------------------------------------------------------------------------
# REGISTRY entries — paste into ronin_metrics.py REGISTRY on approval.
# ---------------------------------------------------------------------------
PROPOSED_REGISTRY_ENTRIES = [
    {
        "pillar": "brush",
        "metric": "Skill_Dead_Ref_Count",
        "source": "~/.claude/data/skill_chain_map.md (DEAD/RETIRED tags)",
        "reducer": _count_skill_dead_refs,
        "tier": "AUTO",
    },
    {
        "pillar": "brush",
        "metric": "Skill_Selector_Token_Weight",
        "source": "~/.claude/skills/*/SKILL.md description bytes",
        "reducer": _skill_selector_token_weight,
        "tier": "AUTO",
    },
]


if __name__ == "__main__":
    dummy_records: list[dict] = []
    dummy_root = Path.cwd()
    dead = _count_skill_dead_refs(dummy_records, dummy_root)
    weight = _skill_selector_token_weight(dummy_records, dummy_root)
    print("validation against live sources (-1 = source missing):")
    print(f"  Skill_Dead_Ref_Count        = {dead}")
    print(f"  Skill_Selector_Token_Weight = {weight} tokens")
