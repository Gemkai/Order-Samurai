#!/usr/bin/env python3
"""execution/score_claude_architecture.py — compute Claude's architecture score
from config/claude_architecture_scorecard.json plus live verifier evidence
(claude_verifier_backlog.md item #13; repo-side template: score_architecture.py).

Scoring model (strict "all required verifiers built", aligned with the sibling
repo scorer score_architecture.py): a category earns its weight only when EVERY
requiredVerifier is built and passing — a single unbuilt required verifier drops
the whole category to 'unmeasured'. FAIL always wins, so a partially-built
category whose built verifiers include a FAIL still blocks (it is never excused
as unmeasured).

  pass        every requiredVerifier built, no FAIL and no WARN rows -> full weight
  advisory    every requiredVerifier built, WARN but no FAIL rows    -> full weight,
              WARNs listed as advisory
  blocking    at least one built verifier emits a FAIL row           -> earns 0
              (zeroed); blocks even while a sibling verifier is still unbuilt
  unmeasured  a requiredVerifier is not built yet AND nothing fails  -> earns 0,
              excluded from the earned/possible calculation; listed as
              'not yet measurable (backlog)'

Score reads as earned/possible-measured, with the unmeasured weight reported as
an explicit separate number. Every lost or unmeasured point carries a
file-backed reason (the verifier row detail, or the missing module name).

As main, the module writes both score artifacts —
artifacts/claude_architecture_score.json and artifacts/claude_architecture_score.md —
then prints [OK]/[WARN]/[FAIL] rows and exits 1 iff any FAIL (a FAIL row exists
iff a measured category was zeroed; a WARN row exists iff unmeasured weight > 0),
so doctor aggregation can consume run_checks() like any other verifier.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import SCORECARD_PATH
from execution.runtime_paths import ARTIFACTS_DIR

SCORE_JSON_NAME = "claude_architecture_score.json"
SCORE_MD_NAME = "claude_architecture_score.md"

_module_counter = itertools.count()


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {
        "status": status,
        "label": label,
        "detail": detail,
    }


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {
        "OK": 0,
        "WARN": 0,
        "FAIL": 0,
    }
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def _run_verifier(rel_path: str, repo_root: Path) -> tuple[str, list[dict]]:
    """Run one verifier's run_checks(). Returns (state, rows):
    state in {'missing' (not built yet), 'error' (import/run failed), 'ran'}.

    Loads by file path (not dotted import) so a sandboxed repo_root works in
    tests without polluting sys.path. A broken verifier is FAIL evidence — it
    must never crash the scorer."""
    module_path = repo_root / rel_path
    if not module_path.is_file():
        return ("missing", [])
    module_name = f"_claude_scored_verifier_{next(_module_counter)}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            return ("error", [_make_result("FAIL", rel_path, "verifier not importable")])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run = getattr(module, "run_checks", None)
        if run is None:
            return ("error", [_make_result("FAIL", rel_path, "verifier exposes no run_checks()")])
        return ("ran", list(run() or []))
    except Exception as exc:  # noqa: BLE001 — evidence, not a crash
        return ("error", [_make_result("FAIL", rel_path, f"verifier raised: {exc!r}")])


def _row_label(row: dict) -> str:
    return str(row.get("label") or row.get("name") or "<unlabelled>")


def _row_detail(row: dict) -> str:
    return str(row.get("detail") or row.get("message") or "")


def compute_score(scorecard: dict, repo_root: Path = ROOT_DIR) -> dict:
    """Pure given the scorecard payload + repo_root. Runs the requiredVerifiers
    that exist under repo_root and returns a JSON-serialisable score report."""
    scoring = scorecard.get("scoring", {})
    target = scoring.get("targetScore", 100)

    categories_out: list[dict] = []
    earned = 0
    possible = 0
    unmeasured_weight = 0

    for category in scorecard.get("categories", []):
        weight = int(category.get("weight", 0))
        verifiers = category.get("requiredVerifiers", []) or []
        existing: list[str] = []
        missing: list[str] = []
        fails: list[dict] = []
        warns: list[dict] = []

        for verifier in verifiers:
            state, rows = _run_verifier(verifier, repo_root)
            if state == "missing":
                missing.append(verifier)
                continue
            existing.append(verifier)
            for row in rows:
                status = (row.get("status") or "").upper()
                evidence = {
                    "verifier": verifier,
                    "label": _row_label(row),
                    "detail": _row_detail(row),
                }
                if status == "FAIL":
                    fails.append(evidence)
                elif status == "WARN":
                    warns.append(evidence)

        # A category is fully built only when it has verifiers and none are
        # missing. FAIL is evaluated first so failing evidence blocks even when
        # a sibling verifier is still unbuilt (preserve FAIL-blocking); a
        # partial category with no failures is unmeasured, not full-weight.
        all_built = bool(verifiers) and not missing
        measured = all_built or bool(fails)
        if fails:
            possible += weight
            status_out, cat_earned = "blocking", 0
            reason = "; ".join(
                f"{f['verifier']} [FAIL] {f['label']}: {f['detail']}" for f in fails
            )
            earned += cat_earned
        elif not all_built:
            status_out, cat_earned = "unmeasured", 0
            unmeasured_weight += weight
            reason = "not yet measurable (backlog): missing " + ", ".join(missing)
        else:
            possible += weight
            if warns:
                status_out, cat_earned = "advisory_warn", weight
                reason = "; ".join(
                    f"{w['verifier']} [WARN] {w['label']}: {w['detail']}" for w in warns
                )
            else:
                status_out, cat_earned = "pass", weight
                reason = ""
            earned += cat_earned

        categories_out.append(
            {
                "id": category.get("id"),
                "label": category.get("label"),
                "weight": weight,
                "measured": measured,
                "earned": cat_earned,
                "status": status_out,
                "existing_verifiers": existing,
                "missing_verifiers": missing,
                "failures": fails,
                "warnings": warns,
                "reason": reason,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "enforcement_mode": scoring.get("enforcementMode"),
        "earned": earned,
        "possible_measured": possible,
        "unmeasured_weight": unmeasured_weight,
        "target_score": target,
        "merge_floor": scoring.get("mergeFloor", 0),
        "release_floor": scoring.get("releaseFloor", 0),
        "blocking_categories": [c["id"] for c in categories_out if c["status"] == "blocking"],
        "advisory_categories": [c["id"] for c in categories_out if c["status"] == "advisory_warn"],
        "unmeasured_categories": [c["id"] for c in categories_out if c["status"] == "unmeasured"],
        "categories": categories_out,
    }


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|")


def render_markdown(report: dict) -> str:
    lines = [
        f"# Claude Architecture Score — {report['earned']}/{report['possible_measured']} measured",
        "",
        f"_Generated {report['generated_at']} · mode: {report['enforcement_mode']}_",
        "",
        f"- **Earned:** {report['earned']} of {report['possible_measured']} measured weight",
        f"- **Unmeasured weight (backlog):** {report['unmeasured_weight']} of {report['target_score']}",
        f"- **Merge floor {report['merge_floor']} / release floor {report['release_floor']}:** "
        f"advisory against target {report['target_score']}",
    ]
    if report["blocking_categories"]:
        lines.append(f"- **Blocking (zeroed) categories:** {', '.join(report['blocking_categories'])}")
    if report["advisory_categories"]:
        lines.append(f"- **Advisory (WARN) categories:** {', '.join(report['advisory_categories'])}")
    lines += [
        "",
        "| Category | Weight | Measured | Earned | Status | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for category in report["categories"]:
        lines.append(
            f"| {_md_cell(category['label'])} | {category['weight']} | "
            f"{'yes' if category['measured'] else 'no'} | {category['earned']} | "
            f"{category['status']} | {_md_cell(category['reason'])} |"
        )

    lost = [c for c in report["categories"] if c["status"] == "blocking"]
    if lost:
        lines += ["", "## Lost points"]
        for category in lost:
            lines.append(f"- {category['label']} (-{category['weight']}): {category['reason']}")

    unmeasured = [c for c in report["categories"] if c["status"] == "unmeasured"]
    if unmeasured:
        lines += ["", "## Not yet measurable (backlog)"]
        for category in unmeasured:
            lines.append(f"- {category['label']} ({category['weight']}): {category['reason']}")

    advisory = [c for c in report["categories"] if c["status"] == "advisory_warn"]
    if advisory:
        lines += ["", "## Advisory warnings"]
        for category in advisory:
            lines.append(f"- {category['label']}: {category['reason']}")

    return "\n".join(lines) + "\n"


def write_artifacts(report: dict, artifacts_dir: Path = ARTIFACTS_DIR) -> tuple[Path, Path]:
    """Emit both score artifacts into artifacts_dir (parameterised for tests)."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifacts_dir / SCORE_JSON_NAME
    md_path = artifacts_dir / SCORE_MD_NAME
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def results_from_report(report: dict) -> list[dict[str, str]]:
    """Doctor-aggregation rows: a FAIL row iff a measured category was zeroed,
    a WARN row iff unmeasured weight > 0, plus one informational OK score row."""
    results: list[dict[str, str]] = []

    for category in report["categories"]:
        if category["status"] == "blocking":
            results.append(
                _make_result(
                    "FAIL",
                    f"claude_architecture.{category['id']}",
                    f"measured category zeroed (-{category['weight']}): {category['reason']}",
                )
            )

    if report["unmeasured_weight"] > 0:
        missing = sorted(
            {
                verifier
                for category in report["categories"]
                if category["status"] == "unmeasured"
                for verifier in category["missing_verifiers"]
            }
        )
        results.append(
            _make_result(
                "WARN",
                "claude_architecture.unmeasured",
                f"{report['unmeasured_weight']} of {report['target_score']} weight "
                f"not yet measurable (backlog); missing {', '.join(missing)}",
            )
        )

    advisory_note = (
        f"; advisory WARN categories: {', '.join(report['advisory_categories'])}"
        if report["advisory_categories"]
        else ""
    )
    results.append(
        _make_result(
            "OK",
            "claude_architecture.score",
            f"earned {report['earned']}/{report['possible_measured']} measured weight "
            f"(unmeasured {report['unmeasured_weight']}){advisory_note}",
        )
    )
    return results


def run_checks(
    repo_root: Path = ROOT_DIR,
    scorecard_payload: dict | None = None,
) -> list[dict[str, str]]:
    if scorecard_payload is None:
        scorecard_payload, error = _load_json(SCORECARD_PATH)
        if error:
            return [_make_result("FAIL", "claude_architecture_scorecard.json", error)]
    report = compute_score(scorecard_payload or {}, repo_root)
    return results_from_report(report)


def main() -> int:
    scorecard_payload, error = _load_json(SCORECARD_PATH)
    if error:
        results = [_make_result("FAIL", "claude_architecture_scorecard.json", error)]
    else:
        report = compute_score(scorecard_payload or {})
        json_path, md_path = write_artifacts(report)
        results = results_from_report(report)
        print(f"Artifacts: {json_path}, {md_path}")

    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
