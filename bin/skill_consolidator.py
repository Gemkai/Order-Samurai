#!/usr/bin/env python3
"""Deterministic skill-consolidator detection mechanism.

The mechanical core of the /skill-consolidator skill (0% success as an LLM
remediation, state/skill_efficacy.json), extracted as a testable mechanism
(RONIN-DETERMINIZATION-PLAN.md, candidate #5 — "mechanical embeddings: cosine +
threshold"). Clustering near-duplicate skills and classifying each group as
clone / language / distinct-function is pure vector arithmetic plus two string
heuristics — no judgement, so it ships with a real eval instead of a 0% LLM run.

This mirrors find_clone_families.py's `_components` (union-find over cosine edges)
and `_classify` (language-suffix collapse + boilerplate-template detection), but
in dependency-free pure Python so the eval pins small vectors and runs without
ChromaDB or numpy. The skill keeps the *judgement* tail: a clone_family group is
a MERGE *candidate* for human verification, never an automatic merge.

What stays LLM/human: opening group members to confirm per-member detail is
truly redundant before merging (the skill's "when in doubt, retain" rule).

Usage:
    python bin/skill_consolidator.py --vectors PATH [--threshold 0.85] [--json]

`--vectors` is a JSON export {"names": [...], "descriptions": [...],
"vectors": [[...], ...]} of the skill embeddings. Read-only: detection only,
never retires or rewrites a skill.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

# Standard near-duplicate edge threshold (raise to 0.88+ for only the tightest groups).
DEFAULT_THRESHOLD = 0.85

# A clone-family must also clear this mean intra-group cosine (matches find_clone_families).
CLONE_COHESION = 0.80

# Suffixes marking legitimate cross-language SDK variants (keep, never merge).
LANG_SUFFIX = re.compile(r"-(py|java|dotnet|net|ts|js|rust|go|cpp|c|rb|swift|kt|cs|php)$")


# ---------------------------------------------------------------------------
# Cosine + clustering (pure)
# ---------------------------------------------------------------------------

def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 if either has zero magnitude."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def cluster(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """Union-find connected components over edges with cosine strictly > threshold.

    Returns the index members of each component with more than one member
    (singletons are not near-duplicate groups), each sorted, ordered by descending
    size then smallest first index — deterministic for a given input.
    """
    n = len(vectors)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if cosine(vectors[i], vectors[j]) > threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    multi = [sorted(m) for m in groups.values() if len(m) > 1]
    return sorted(multi, key=lambda m: (-len(m), m[0]))


# ---------------------------------------------------------------------------
# Classification (pure)
# ---------------------------------------------------------------------------

def mean_intra_cosine(members: list[int], vectors: list[list[float]]) -> float:
    """Mean pairwise cosine within a group; 0.0 for a degenerate <2-member group."""
    pairs = [
        cosine(vectors[a], vectors[b])
        for idx, a in enumerate(members)
        for b in members[idx + 1:]
    ]
    return sum(pairs) / len(pairs) if pairs else 0.0


def is_language_family(names: list[str]) -> bool:
    """True if members collapse to fewer stems once the language suffix is stripped."""
    stems = {LANG_SUFFIX.sub("", x) for x in names}
    return len(stems) < len(names)


def _desc_body(description: str) -> str:
    """Strip a leading 'name: ' prefix the corpus uses, leaving the description body."""
    return description.split(":", 1)[1].strip() if ":" in description[:60] else description


def has_template_descriptions(descriptions: list[str]) -> bool:
    """True if the first ~6 words are near-identical across members (a generated template).

    Heuristic from find_clone_families: distinct openings number at most n//4 (or 1),
    i.e. the group shares boilerplate rather than each member writing its own intro.
    """
    firsts = [" ".join(_desc_body(d).split()[:6]).lower() for d in descriptions]
    return len(set(firsts)) <= max(1, len(descriptions) // 4)


def classify(members: list[int], names: list[str], descriptions: list[str],
             vectors: list[list[float]]) -> dict:
    """Classify one near-duplicate group as clone / language / distinct-function.

    Precedence mirrors the skill: a language family is recognised first (cross-SDK
    variants are intentionally distinct), then a clone family requires BOTH a
    boilerplate-template signal AND cohesion >= CLONE_COHESION; everything else is
    a distinct-function family that must not be merged.
    """
    mnames = [names[i] for i in members]
    mdescs = [descriptions[i] for i in members]
    mean_cos = mean_intra_cosine(members, vectors)
    template = has_template_descriptions(mdescs)

    if is_language_family(mnames):
        kind = "language_family"
    elif template and mean_cos >= CLONE_COHESION:
        kind = "clone_family"
    else:
        kind = "distinct_function"

    return {
        "kind": kind,
        "members": sorted(mnames),
        "mean_cos": round(mean_cos, 3),
        "template_descs": template,
        "size": len(members),
    }


def analyze(names: list[str], descriptions: list[str], vectors: list[list[float]],
            *, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """Detect and classify every near-duplicate group in the corpus.

    Returns {"threshold", "groups": [classified group, ...]}. Only clone_family
    groups are MERGE candidates; language/distinct groups are reported as KEEP.
    Pure and idempotent: same corpus + threshold -> identical result.
    """
    groups = [
        classify(members, names, descriptions, vectors)
        for members in cluster(vectors, threshold)
    ]
    return {"threshold": threshold, "groups": groups}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    buckets = {"clone_family": [], "language_family": [], "distinct_function": []}
    for g in report["groups"]:
        buckets[g["kind"]].append(g)

    lines = [f"near-duplicate groups at cosine>{report['threshold']}: {len(report['groups'])}"]
    titles = {
        "clone_family": "MERGE CANDIDATES (clone_family — mechanical template copies)",
        "language_family": "KEEP — language families (cross-language SDK variants)",
        "distinct_function": "KEEP — distinct-function families (same theme, different jobs)",
    }
    for kind, title in titles.items():
        items = buckets[kind]
        lines.append(f"\n{title}: {len(items)} groups")
        for g in items:
            members = " | ".join(g["members"][:6]) + (" …" if g["size"] > 6 else "")
            lines.append(f"  ({g['size']}, cos={g['mean_cos']}) {members}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic skill-consolidator detection mechanism")
    parser.add_argument("--vectors", type=Path, required=True,
                        help='JSON export {"names", "descriptions", "vectors"} of skill embeddings')
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"cosine edge threshold (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        data = json.loads(args.vectors.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"skill-consolidator: cannot read vectors {args.vectors}: {exc}", file=sys.stderr)
        return 1

    report = analyze(
        data.get("names", []),
        data.get("descriptions", []),
        data.get("vectors", []),
        threshold=args.threshold,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
