#!/usr/bin/env python3
"""rival_fixture_review — weekly seeded-fault self-audit for rival (Phase 4).

Mirrors surface_proposal_review.py's --list/--record CLI shape exactly, adapted from
"list a pending queue item / attach a verdict to it" to "list an unrun fixture this
round / grade rival's verdict against the seeded answer". Same reason surface_proposal_
review.py exists at all: the deterministic bookkeeping (which fixture is due, whether
the round should run yet, was the verdict actually right) must not depend on a model
getting JSON right — only the rival CALL ITSELF (which this script cannot make; see
module docstring's "why not a single Python script" note) needs a model.

    --list    the fixture(s) due this round, with everything rival needs to attack
              them (self-contained: an absolute path to each fixture's bundled
              evidence file, not a live-production path — see fixture design note)
    --record  one rival verdict, graded against the fixture's seeded expected_verdict
              and appended to state/rival_self_audit.jsonl

Fixture design note: each fixture under tests/rival_fixtures/<id>/ bundles its own
evidence.jsonl (or similar) rather than pointing rival at live, mutable production
state — a scout_finding claiming a specific count against a live metric would drift
out of sync with reality the moment production data changes, breaking the fixture's
"this seeded answer is correct" guarantee. --list resolves each fixture's
evidence.source_file to an absolute path under the fixture directory before handing
it to rival, so rival's own Read/Grep/Bash tools resolve it unambiguously regardless
of invocation cwd.

Own weekly spacing guard (MIN_ROUND_SPACING_HOURS, default 168, env-overridable) —
same reasoning as run_experiment_cycle.py and self_harness_cycle.py: the cadence is
this loop's own property, not a fact about whatever schedule invokes it. A spacing-
guard skip writes NOTHING to rival_audit_lineage.jsonl (see that module's DECISIONS
comment for why). The round-start lineage row is written ONCE by --list, when the
round is confirmed to proceed — not per-fixture, and not deferred to --record.

Usage:
  python3 bin/rival_fixture_review.py --list
  python3 bin/rival_fixture_review.py --record --id <fixture_id> --round <n> \
      --verdict REFUTED --confidence high --reasoning "..." --evidence "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))
_OS_ROOT = Path(__file__).resolve().parents[1]
if str(_OS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OS_ROOT))

from agentica_core import rival_audit_lineage  # noqa: E402

_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT", str(_OS_ROOT)))
_STATE = _ROOT / "state"
_SELF_AUDIT = _STATE / "rival_self_audit.jsonl"
_FIXTURES_ROOT = _ROOT / "tests" / "rival_fixtures"

VERDICTS = ("CONFIRMED", "REFUTED", "SUSPECT")
CONFIDENCES = ("high", "medium", "low")

MIN_ROUND_SPACING_HOURS = float(os.environ.get("RIVAL_AUDIT_MIN_SPACING_HOURS") or 168)


def _discover_fixtures(fixtures_root: Path) -> list[str]:
    if not fixtures_root.is_dir():
        return []
    return sorted(p.name for p in fixtures_root.iterdir()
                  if p.is_dir() and (p / "scout_finding.json").is_file()
                  and (p / "expected.json").is_file())


def _load_fixture(fixtures_root: Path, fixture_id: str) -> dict:
    fdir = fixtures_root / fixture_id
    finding = json.loads((fdir / "scout_finding.json").read_text(encoding="utf-8"))
    # Resolve a relative source_file to an absolute path under the fixture dir, so
    # rival's Read/Grep/Bash tools find it regardless of invocation cwd — see module
    # docstring's fixture design note.
    ev = finding.get("evidence")
    if isinstance(ev, dict):
        sf = ev.get("source_file")
        if isinstance(sf, str) and not sf.startswith("/"):
            ev["source_file"] = str((fdir / sf).resolve())
    return finding


def _rounds_already_covered(fixture_id: str, round_no: int, self_audit_path: Path) -> bool:
    if not self_audit_path.exists():
        return False
    for line in self_audit_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("fixture_id") == fixture_id and row.get("round") == round_no:
            return True
    return False


def list_pending(
    fixtures_root: Optional[Path] = None,
    lineage_path: Optional[Path] = None,
    self_audit_path: Optional[Path] = None,
    min_spacing_hours: Optional[float] = None,
    dry_run: bool = False,
) -> dict:
    """One round: spacing guard, then fixtures due this round. Returns the CLI's
    stdout payload — {"ran", "reason", "round", "fixtures": [{fixture_id, scout_finding}]}.

    dry_run computes everything (spacing, round number, fixture discovery) but skips
    the lineage write either way — unlike surface_proposal_review.py's side-effect-free
    --list, this one writes a round-start row, so it needs the same
    `if not dry_run: append_entry(...)` discipline run_experiment_cycle.py uses. A dry
    run still reports a real round number and fixture list so the caller can exercise
    the rival-call path without consuming the round.
    """
    fixtures_root = fixtures_root if fixtures_root is not None else _FIXTURES_ROOT
    self_audit_path = self_audit_path if self_audit_path is not None else _SELF_AUDIT
    spacing = min_spacing_hours if min_spacing_hours is not None else MIN_ROUND_SPACING_HOURS

    h = rival_audit_lineage.hours_since_last_round(lineage_path)
    if h is not None and h < spacing:
        return {"ran": False, "reason": f"last round {h:.1f}h ago < {spacing}h spacing",
                "round": None, "fixtures": []}

    round_no = 1 + max(
        (e.get("round", 0) for e in rival_audit_lineage.iter_entries(lineage_path)), default=0
    )

    all_fixtures = _discover_fixtures(fixtures_root)
    pending_ids = [fid for fid in all_fixtures
                   if not _rounds_already_covered(fid, round_no, self_audit_path)]

    if not pending_ids:
        if not dry_run:
            rival_audit_lineage.append_entry(
                {"round": round_no, "decision": "skipped_no_candidate",
                 "reason": "no fixture due this round"},
                lineage_path,
            )
        return {"ran": True, "reason": "no pending fixture", "round": round_no, "fixtures": []}

    if not dry_run:
        rival_audit_lineage.append_entry(
            {"round": round_no, "decision": "ran",
             "reason": f"{len(pending_ids)} fixture(s) due"},
            lineage_path,
        )
    fixtures = [
        {"fixture_id": fid, "scout_finding": _load_fixture(fixtures_root, fid)}
        for fid in pending_ids
    ]
    return {"ran": True, "reason": "", "round": round_no, "fixtures": fixtures}


def record(
    fixture_id: str,
    round_no: int,
    verdict: str,
    *,
    confidence: str,
    reasoning: str,
    evidence: str,
    fixtures_root: Optional[Path] = None,
    self_audit_path: Optional[Path] = None,
) -> dict:
    """Grade rival's verdict against the fixture's seeded expected_verdict and append
    one row to state/rival_self_audit.jsonl. Raises on an unknown fixture id — a
    graded row that silently drops off the wrong id is worse than a loud failure."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict {verdict!r} not one of {VERDICTS}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"confidence {confidence!r} not one of {CONFIDENCES}")

    fixtures_root = fixtures_root if fixtures_root is not None else _FIXTURES_ROOT
    self_audit_path = self_audit_path if self_audit_path is not None else _SELF_AUDIT

    expected_path = fixtures_root / fixture_id / "expected.json"
    if not expected_path.is_file():
        raise KeyError(f"no fixture with id {fixture_id!r}")
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    seeded_verdict = expected["expected_verdict"]

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "round": round_no,
        "fixture_id": fixture_id,
        "seeded_verdict": seeded_verdict,
        "actual_verdict": verdict,
        "confidence": confidence,
        "passed": verdict == seeded_verdict,
        "reasoning": reasoning,
        "evidence": evidence,
    }
    self_audit_path.parent.mkdir(parents=True, exist_ok=True)
    with open(self_audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="List due rival fixtures / record a graded verdict.")
    ap.add_argument("--list", action="store_true", help="print this round's due fixtures as JSON")
    ap.add_argument("--dry-run", action="store_true", help="with --list: compute but write nothing")
    ap.add_argument("--record", action="store_true", help="grade and record one rival verdict")
    ap.add_argument("--id", help="fixture id (with --record)")
    ap.add_argument("--round", type=int, help="round number from --list's output (with --record)")
    ap.add_argument("--verdict", choices=VERDICTS)
    ap.add_argument("--confidence", choices=CONFIDENCES, default="high")
    ap.add_argument("--reasoning", default="")
    ap.add_argument("--evidence", default="")
    args = ap.parse_args()

    if args.record:
        if not (args.id and args.round is not None and args.verdict):
            ap.error("--record requires --id, --round, and --verdict")
        stored = record(args.id, args.round, args.verdict, confidence=args.confidence,
                        reasoning=args.reasoning, evidence=args.evidence)
        print(json.dumps(stored, indent=2))
        return 0

    print(json.dumps(list_pending(dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
