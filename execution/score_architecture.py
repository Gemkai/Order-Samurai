#!/usr/bin/env python3
"""execution/score_architecture.py — compute the architecture score from the
scorecard contract (config/architecture_scorecard.json) + live verifier evidence,
and emit JSON + Markdown score artifacts (verifier backlog item #14).

Scoring semantics follow the scorecard's `enforcementMode`:
"advisory-until-verifiers-exist".

  pass          all the category's verifiers exist and emit no FAIL  → earns full weight
  advisory_warn verifier(s) exist, emit WARN but no FAIL             → earns full weight (flagged)
  blocking      verifier(s) exist and emit at least one FAIL         → earns 0 (real regression)
  advisory_gap  a required verifier is not built yet                 → earns 0, NOT a failure

Score = sum of earned category weights (weights sum to targetScore = 100). Unbuilt
verifiers simply can't earn their weight; the score climbs as the stack is built
and passes. `achievable_now` is the ceiling given currently-built verifiers, so an
operator can read "earned / achievable" separately from "earned / 100".

Exit code is 0 on a successful scoring run (this is a reporter/scorer, not a gate —
a read-only mechanism candidate for metric:brush:Architecture_Scorecard_Grade);
non-zero only when the scorecard itself can't be read.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import REPO_ROOT, CONFIG_DIR, ARTIFACTS_DIR

SCORECARD_PATH = CONFIG_DIR / "architecture_scorecard.json"


def _verifier_module(rel_path: str) -> str:
    """'execution/verify_root_hygiene.py' -> 'execution.verify_root_hygiene'."""
    stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return stem.replace("/", ".").replace("\\", ".")


def _run_verifier(rel_path: str, repo_root: Path) -> tuple[str, list[dict]]:
    """Run one verifier's run_checks(). Returns (state, results):
    state ∈ {'missing' (not built), 'error' (import/run failed), 'ran'}."""
    if not (repo_root / rel_path).is_file():
        return ("missing", [])
    try:
        mod = importlib.import_module(_verifier_module(rel_path))
        run = getattr(mod, "run_checks", None)
        if run is None:
            return ("error", [{"status": "FAIL", "label": rel_path,
                               "message": "verifier exposes no run_checks()"}])
        return ("ran", list(run() or []))
    except Exception as exc:  # a broken verifier must never crash the scorer
        return ("error", [{"status": "FAIL", "label": rel_path,
                           "message": f"verifier raised: {exc!r}"}])


def compute_score(scorecard: dict, repo_root: Path = REPO_ROOT) -> dict:
    """Pure given the scorecard + repo_root. Runs the real verifiers named in the
    scorecard and returns a structured, JSON-serialisable score report."""
    scoring = scorecard.get("scoring", {})
    target = scoring.get("targetScore", 100)

    categories_out: list[dict] = []
    earned = 0
    achievable = 0  # weight of categories whose required verifiers all exist

    for cat in scorecard.get("categories", []):
        weight = cat.get("weight", 0)
        verifiers = cat.get("requiredVerifiers", [])
        missing_artifacts = [a for a in cat.get("requiredArtifacts", [])
                             if not (repo_root / a).is_file()]
        missing_verifiers, fails, warns = [], [], []
        all_built = bool(verifiers)

        for v in verifiers:
            state, results = _run_verifier(v, repo_root)
            if state == "missing":
                all_built = False
                missing_verifiers.append(v)
            for r in results:
                st = (r.get("status") or "").upper()
                if st == "FAIL":
                    fails.append({"verifier": v, **r})
                elif st == "WARN":
                    warns.append({"verifier": v, **r})

        if not all_built:
            status, cat_earned = "advisory_gap", 0
        else:
            achievable += weight
            if fails:
                status, cat_earned = "blocking", 0
            elif warns:
                status, cat_earned = "advisory_warn", weight
            else:
                status, cat_earned = "pass", weight
            earned += cat_earned

        categories_out.append({
            "id": cat.get("id"), "label": cat.get("label"),
            "weight": weight, "earned": cat_earned, "status": status,
            "missing_verifiers": missing_verifiers,
            "missing_artifacts": missing_artifacts,
            "failures": fails, "warnings": warns,
        })

    merge_floor = scoring.get("mergeFloor", 0)
    release_floor = scoring.get("releaseFloor", 0)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "enforcement_mode": scoring.get("enforcementMode"),
        "score": earned,
        "target_score": target,
        "achievable_now": achievable,
        "merge_floor": merge_floor,
        "release_floor": release_floor,
        "meets_merge_floor": earned >= merge_floor,
        "meets_release_floor": earned >= release_floor,
        "blocking_categories": [c["id"] for c in categories_out if c["status"] == "blocking"],
        "advisory_gaps": [c["id"] for c in categories_out if c["status"] == "advisory_gap"],
        "categories": categories_out,
    }


def render_markdown(report: dict) -> str:
    icon = {"pass": "✅", "advisory_warn": "🟡", "blocking": "❌", "advisory_gap": "⬜"}
    lines = [
        f"# Architecture Score — {report['score']}/{report['target_score']}",
        "",
        f"_Generated {report['generated_at']} · mode: {report['enforcement_mode']}_",
        "",
        f"- **Score:** {report['score']} / {report['target_score']} "
        f"(achievable now: {report['achievable_now']})",
        f"- **Merge floor {report['merge_floor']}:** "
        f"{'PASS' if report['meets_merge_floor'] else 'BELOW'}",
        f"- **Release floor {report['release_floor']}:** "
        f"{'PASS' if report['meets_release_floor'] else 'BELOW'}",
    ]
    if report["blocking_categories"]:
        lines.append(f"- **Blocking failures:** {', '.join(report['blocking_categories'])}")
    if report["advisory_gaps"]:
        lines.append(f"- **Advisory gaps (verifier not built):** {', '.join(report['advisory_gaps'])}")
    lines += ["", "| Category | Weight | Earned | Status | Notes |", "|---|---|---|---|---|"]
    for c in report["categories"]:
        notes = []
        if c["missing_verifiers"]:
            notes.append("missing: " + ", ".join(v.split("/")[-1] for v in c["missing_verifiers"]))
        if c["failures"]:
            notes.append(f"{len(c['failures'])} FAIL")
        if c["warnings"]:
            notes.append(f"{len(c['warnings'])} WARN")
        lines.append(f"| {c['label']} | {c['weight']} | {c['earned']} | "
                     f"{icon.get(c['status'], '')} {c['status']} | {'; '.join(notes)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    try:
        scorecard = json.loads(SCORECARD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL: cannot read scorecard at {SCORECARD_PATH}: {exc}", file=sys.stderr)
        return 1

    report = compute_score(scorecard)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS_DIR / "architecture_score.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (ARTIFACTS_DIR / "architecture_score.md").write_text(
        render_markdown(report), encoding="utf-8")

    print(f"Architecture score: {report['score']}/{report['target_score']} "
          f"(achievable now {report['achievable_now']}) | "
          f"merge-floor {report['merge_floor']}: "
          f"{'PASS' if report['meets_merge_floor'] else 'BELOW'} | "
          f"blocking: {report['blocking_categories'] or 'none'} | "
          f"advisory gaps: {len(report['advisory_gaps'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
