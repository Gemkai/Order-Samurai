#!/usr/bin/env python3
"""harness_eval_runner — run the evalsuite and report per-task pass fractions (M3).

The measurement half of the self-harness gate. For every task: materialise its fixture into a
fresh temp workspace, fingerprint the seeded files, run the grader, re-fingerprint, repeat
`repeats` times, report the pass fraction. Fractions, not booleans: 2 passes out of 3 is 2/3
(only b1 is stochastic today, but the contract is uniform).

Anti-gaming properties, in order of the harm they block:
  - Seeded-file fingerprints: a grader (or anything a grader calls) that edits its own answer key
    fails the task outright, whatever else happened. (The video's answer-key trap.)
  - PER-TASK results are the payload. The acceptance rule in self_harness_cycle compares every
    task individually — the video's third candidate broke one task while the aggregate barely
    moved, and only per-task inspection caught it. Split means are computed here for reporting,
    never for gating.
  - Graders never read the editable surface (coupling audit in tests); knob sensitivity flows
    through the consumers, which honour OS_HARNESS_<KEY> env overrides — how a candidate is
    pinned without editing the file being measured.

Usage:
  python3 bin/harness_eval_runner.py --json          # print payload to stdout
  python3 bin/harness_eval_runner.py --baseline      # atomic-write state/harness_eval_baseline.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))
_OS_ROOT = Path(__file__).resolve().parents[1]
if str(_OS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OS_ROOT))

from agentica_core import harness_config  # noqa: E402
from agentica_core.atomic import atomic_json_write  # noqa: E402
from evalsuite import fixtures, graders  # noqa: E402

_SUITE = _OS_ROOT / "evalsuite"
_BASELINE = _OS_ROOT / "state" / "harness_eval_baseline.json"


def load_tasks() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((_SUITE / "tasks").glob("*.json"))]


def load_split() -> dict:
    return json.loads((_SUITE / "split.json").read_text(encoding="utf-8"))


def _fingerprint(paths: list[Path]) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}


def run_task_once(task: dict) -> tuple[bool, str]:
    """One repeat: (passed, note). A tampered seed fails regardless of the grader's verdict."""
    fixture_fn = getattr(fixtures, task["fixture"])
    grader_fn = getattr(graders, task["grader"].removeprefix("graders."))

    workspace = Path(tempfile.mkdtemp(prefix=f"evalsuite_{task['id']}_"))
    try:
        seeded = fixture_fn(workspace)
        before = _fingerprint(seeded)
        try:
            passed = bool(grader_fn(workspace))
            note = ""
        except Exception as exc:  # a grader crash is a suite defect, surfaced not swallowed
            passed, note = False, f"grader_error: {exc}"
        after = _fingerprint(seeded)
        if before != after:
            return False, "seed_tampered: fingerprint changed during grading"
        return passed, note
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_suite(task_filter: set[str] | None = None) -> dict:
    """Run every task (or the filtered subset); return the per-task payload."""
    tasks = load_tasks()
    split = load_split()
    split_of = {tid: "held_in" for tid in split["held_in"]}
    split_of.update({tid: "held_out" for tid in split["held_out"]})

    results: dict[str, dict] = {}
    for task in tasks:
        if task_filter is not None and task["id"] not in task_filter:
            continue
        repeats = max(1, int(task.get("repeats", 1)))
        passes, notes = 0, []
        for _ in range(repeats):
            ok, note = run_task_once(task)
            passes += int(ok)
            if note:
                notes.append(note)
        results[task["id"]] = {
            "fraction": round(passes / repeats, 3),
            "repeats": repeats,
            "kind": task["kind"],
            "group": task["group"],
            "split": split_of.get(task["id"], "unassigned"),
            **({"notes": sorted(set(notes))} if notes else {}),
        }

    def _mean(which: str) -> float | None:
        vals = [r["fraction"] for r in results.values() if r["split"] == which]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Effective (override-applied) surface: a baseline run has no OS_HARNESS_* overrides so
        # this equals surface_fingerprint(), but a pinned candidate run gets a fingerprint that
        # reflects the surface it actually ran with (lineage/traceability, not acceptance math).
        "harness_fingerprint": harness_config.effective_surface_fingerprint(),
        "results": results,
        "held_in_mean": _mean("held_in"),
        "held_out_mean": _mean("held_out"),
        "red_miners": sorted(t for t, r in results.items() if r["kind"] == "miner" and r["fraction"] < 1),
        "red_guards": sorted(t for t, r in results.items() if r["kind"] == "guard" and r["fraction"] < 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the harness eval suite.")
    ap.add_argument("--json", action="store_true", help="print payload to stdout")
    ap.add_argument("--baseline", action="store_true", help="write state/harness_eval_baseline.json")
    args = ap.parse_args()

    payload = run_suite()
    if args.baseline:
        atomic_json_write(_BASELINE, payload)
        print(f"wrote {_BASELINE}")
    if args.json or not args.baseline:
        print(json.dumps(payload, indent=2))
    # Exit code reflects GUARD health only: red miners are the suite working as designed
    # (they encode known weaknesses); a red guard means the harness regressed or the suite broke.
    return 1 if payload["red_guards"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
