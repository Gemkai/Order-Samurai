#!/usr/bin/env python3
"""skill_improvement_scan.py — the deterministic FRONT half of the keiko skill-improvement cycle.

The reflex engine deposits a ground-truth-anchored case on every mechanism->skill fallback
run (state/eval_corpus.jsonl, written by api/src/reflex-engine.ts _appendExecLog): the
deterministic mechanism diagnosis, the skill that was handed it, and whether the metric
actually moved. This script reads that corpus and identifies which skills are worth trying to
improve — skills that repeatedly FAIL even when given a correct diagnosis to start from.

It is deliberately ONLY the analysis/proposal stage: pure arithmetic over the corpus, testable,
no LLM, no side effects beyond writing state/skill_improvement_candidates.json. The expensive
back half — generate a skill revision, replay it against these cases, ship ONLY if it beats the
incumbent — is an LLM judgment call gated by human review (see docs/CYCLE-TERMINOLOGY.md); it
consumes this file, it is not this file.

Runs once per keiko (bin/meditation_overnight.sh), after the cycle loop. No-op on an empty or
absent corpus (exit 0) so it is safe to wire before any cases have accumulated.

Usage:
    python3 bin/skill_improvement_scan.py [--min-cases N] [--max-rate R] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 guard

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(Path(__file__).resolve().parents[1])))
CORPUS_PATH = _ROOT / "state" / "eval_corpus.jsonl"
CANDIDATES_PATH = _ROOT / "state" / "skill_improvement_candidates.json"

# A skill needs at least this many corpus cases before its success rate is trustworthy
# (mirrors reflex_eureka._MIN_RUNS = 5 — same "don't act on noise" bar the maturity ladder uses).
DEFAULT_MIN_CASES = 5
# Flag a skill whose corpus success rate is at or below this. 0.5 = "fails at least half the
# time even when handed a deterministic diagnosis" — a clear improvement candidate.
DEFAULT_MAX_RATE = 0.5
# Cap the failing-case excerpts carried per candidate so the file stays bounded.
_MAX_FAILING_CASES = 8


def _read_corpus(path: Path = CORPUS_PATH) -> list[dict]:
    """Read eval_corpus.jsonl. Missing file or malformed lines degrade to []/skip — the
    corpus is an analytics sink that may not exist yet, never a hard dependency."""
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # skip a torn/partial append, keep the rest
            if isinstance(rec, dict):
                rows.append(rec)
    except OSError:
        return []
    return rows


def scan(rows: list[dict], min_cases: int, max_rate: float) -> list[dict]:
    """Group corpus cases by skill; return improvement candidates, worst rate first.

    A candidate = a skill with >= min_cases cases whose improved-rate <= max_rate. Each carries
    the failing cases' diagnoses — the concrete eval material the back-half revision must fix."""
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        skill = r.get("skill")
        if isinstance(skill, str) and skill:
            by_skill[skill].append(r)

    candidates: list[dict] = []
    for skill, cases in by_skill.items():
        total = len(cases)
        if total < min_cases:
            continue
        improved = sum(1 for c in cases if c.get("improved") is True)
        rate = improved / total
        if rate > max_rate:
            continue
        failing = [c for c in cases if c.get("improved") is not True]
        candidates.append({
            "skill": skill,
            "cases": total,
            "improved": improved,
            "success_rate": round(rate, 3),
            "command": next((c.get("command") for c in cases if c.get("command")), None),
            # Concrete failures for the revision stage: the diagnosis the mechanism produced +
            # the metric it was meant to move. Deduped-lite by truncation, capped for size.
            "failing_cases": [
                {
                    "metric": c.get("metric"),
                    "mechanism_script": c.get("mechanism_script"),
                    "diagnosis": (c.get("diagnosis") or "")[:1000],
                    "status": c.get("status"),
                }
                for c in failing[:_MAX_FAILING_CASES]
            ],
        })

    candidates.sort(key=lambda c: (c["success_rate"], -c["cases"]))
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan the eval corpus for skill-improvement candidates.")
    ap.add_argument("--min-cases", type=int, default=DEFAULT_MIN_CASES)
    ap.add_argument("--max-rate", type=float, default=DEFAULT_MAX_RATE)
    ap.add_argument("--json", action="store_true", help="print the candidates JSON to stdout")
    args = ap.parse_args()

    rows = _read_corpus()
    candidates = scan(rows, args.min_cases, args.max_rate)

    report = {
        "corpus_cases": len(rows),
        "min_cases": args.min_cases,
        "max_rate": args.max_rate,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    try:
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATES_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"skill_improvement_scan: could not write {CANDIDATES_PATH}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(report, indent=2))
    elif not rows:
        print("skill_improvement_scan: corpus empty — no candidates (no-op).")
    else:
        print(f"skill_improvement_scan: {len(rows)} cases → {len(candidates)} candidate(s) "
              f"→ {CANDIDATES_PATH}")
        for c in candidates:
            print(f"  {c['skill']}: {c['improved']}/{c['cases']} improved "
                  f"({c['success_rate']:.0%}) — {len(c['failing_cases'])} failing case(s) attached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
