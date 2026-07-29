"""Backlog item 14: keep the Claude enforcement pack internally coherent.

This verifier does not audit ~/.claude behavior; it audits the *pack itself* —
the policy JSONs, scorecard, backlog, and report that together drive every other
`verify_claude_*` gate. It catches the drift class where a scorecard promises a
verifier the backlog never planned, a policy names an artifact that was deleted,
or the backlog's own status note goes stale as verifiers land.

Checks (all import their target paths from ``claude_runtime_target`` — no path is
re-declared here):
  (a) every policy in ALL_POLICY_PATHS parses as JSON            -> FAIL if not
  (b) scorecard required*Artifacts that point INSIDE this repo
      (config/, backlog/, reports/, execution/) exist on disk    -> FAIL if missing
  (c) every execution/ verifier a pack policy names either exists
      or is listed in the backlog implementation order.
        - missing but backlogged  -> ONE aggregated WARN
        - missing, unbacklogged, named by a severity-bearing policy -> FAIL
        - missing, unbacklogged, named only by the (advisory) scorecard
          -> WARN (scorecard/backlog drift; scorecard is
             "advisory-until-claude-verifiers-exist")
      Runtime-side ``scripts/*.py`` references are checked under runtime_root()
      and only WARN when absent (a missing runtime root is itself a WARN).
  (d) REPORT_PATH and BACKLOG_PATH exist                         -> FAIL if missing
  (e) the backlog STATUS AUDIT claim ("none of the ... verifiers ... exist")
      stays truthful; if a backlogged verifier now exists         -> WARN (stale)

Exit code is 1 iff any FAIL, mirroring the sibling verifiers.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import (  # type: ignore[attr-defined]
    ALL_POLICY_PATHS,
    BACKLOG_PATH,
    REPORT_PATH,
    ROOT_DIR as TARGET_ROOT_DIR,
    SCORECARD_PATH,
    runtime_root,
)

# Only these scorecard artifact prefixes live inside the Order Samurai repo; the
# rest (scripts/, data/, settings.json, CLAUDE.md, ...) are runtime-side under
# ~/.claude and are governed by other checks, not by check (b).
REPO_ARTIFACT_PREFIXES = ("config/", "backlog/", "reports/", "execution/")


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def iter_strings(payload: object):
    """Yield every string leaf in a nested JSON structure."""
    if isinstance(payload, dict):
        for value in payload.values():
            yield from iter_strings(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from iter_strings(value)
    elif isinstance(payload, str):
        yield payload


def collect_verifier_refs(payload: object) -> set[str]:
    """Repo-relative ``execution/*.py`` references named anywhere in a policy."""
    return {
        s
        for s in iter_strings(payload)
        if s.startswith("execution/") and s.endswith(".py")
    }


def collect_script_refs(payload: object) -> set[str]:
    """Runtime-side ``scripts/*.py`` references named anywhere in a policy."""
    return {
        s
        for s in iter_strings(payload)
        if s.startswith("scripts/") and s.endswith(".py")
    }


def collect_inside_repo_artifacts(scorecard_payload: dict) -> set[str]:
    """Scorecard required*Artifacts entries that resolve inside this repo."""
    found: set[str] = set()
    for category in scorecard_payload.get("categories", []):
        for key in ("requiredPackArtifacts", "requiredRuntimeArtifacts"):
            for artifact in category.get(key, []):
                if isinstance(artifact, str) and artifact.startswith(REPO_ARTIFACT_PREFIXES):
                    found.add(artifact)
    return found


def parse_backlog_verifiers(backlog_text: str) -> set[str]:
    """Basenames of every ``*.py`` the backlog enumerates (section headers +
    the implementation-order list both name them in backticks)."""
    return {Path(m).name for m in re.findall(r"`([^`]+\.py)`", backlog_text)}


def status_audit_claims_none_exist(backlog_text: str) -> bool:
    """True when the backlog's STATUS AUDIT still asserts no verifiers exist."""
    return bool(
        re.search(r"none of the[^.]*verifiers[^.]*exist", backlog_text, re.IGNORECASE)
    )


def run_checks(
    *,
    policy_paths: tuple[Path, ...] = ALL_POLICY_PATHS,
    scorecard_path: Path = SCORECARD_PATH,
    backlog_path: Path = BACKLOG_PATH,
    report_path: Path = REPORT_PATH,
    repo_root: Path = TARGET_ROOT_DIR,
    runtime_root_dir: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    if runtime_root_dir is None:
        runtime_root_dir = runtime_root()

    # (a) every pack policy loads as JSON.
    payloads: dict[Path, dict] = {}
    load_failures: list[str] = []
    for path in policy_paths:
        payload, error = _load_json(path)
        if error:
            load_failures.append(f"{path.name} ({error})")
        else:
            payloads[path] = payload or {}
    if load_failures:
        results.append(
            _make_result("FAIL", "pack.policies-load", ", ".join(sorted(load_failures)))
        )
    else:
        results.append(
            _make_result(
                "OK",
                "pack.policies-load",
                f"all {len(policy_paths)} pack policies parse as JSON",
            )
        )

    # (b) scorecard artifacts that live inside this repo must exist.
    scorecard_payload = payloads.get(scorecard_path)
    if scorecard_payload is None:
        # already reported as a load failure above; skip artifact resolution.
        pass
    else:
        repo_artifacts = collect_inside_repo_artifacts(scorecard_payload)
        missing_artifacts = sorted(
            a for a in repo_artifacts if not (repo_root / a).exists()
        )
        if missing_artifacts:
            results.append(
                _make_result(
                    "FAIL",
                    "pack.scorecard-artifacts",
                    "scorecard names in-repo artifact(s) that are missing: "
                    + ", ".join(missing_artifacts),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "pack.scorecard-artifacts",
                    f"all {len(repo_artifacts)} in-repo scorecard artifacts exist",
                )
            )

    # (c) verifier references vs. disk vs. the backlog roadmap.
    backlog_text = ""
    backlog_exists = backlog_path.is_file()
    if backlog_exists:
        backlog_text = backlog_path.read_text(encoding="utf-8", errors="ignore")
    backlog_verifiers = parse_backlog_verifiers(backlog_text)

    scorecard_refs = collect_verifier_refs(scorecard_payload or {})
    policy_refs: set[str] = set()
    for path, payload in payloads.items():
        if path == scorecard_path:
            continue
        policy_refs |= collect_verifier_refs(payload)
    all_refs = scorecard_refs | policy_refs

    missing_backlogged: list[str] = []
    fail_orphans: list[str] = []
    scorecard_only_orphans: list[str] = []
    for ref in all_refs:
        if (repo_root / ref).exists():
            continue
        if Path(ref).name in backlog_verifiers:
            missing_backlogged.append(ref)
        elif ref in policy_refs:
            # A severity-bearing policy names a verifier that exists nowhere and
            # is planned nowhere: a hard pack orphan.
            fail_orphans.append(ref)
        else:
            # Named only by the advisory scorecard, and absent from the backlog:
            # scorecard/backlog drift, not a blocking failure.
            scorecard_only_orphans.append(ref)

    present_refs = sorted(r for r in all_refs if (repo_root / r).exists())
    results.append(
        _make_result(
            "OK",
            "pack.verifier-refs-present",
            f"{len(present_refs)} referenced verifier(s) present on disk",
        )
    )
    if fail_orphans:
        results.append(
            _make_result(
                "FAIL",
                "pack.verifier-refs-orphaned",
                "policy names verifier(s) that are neither implemented nor backlogged: "
                + ", ".join(sorted(fail_orphans)),
            )
        )
    if missing_backlogged:
        results.append(
            _make_result(
                "WARN",
                "pack.verifier-refs-backlogged",
                f"{len(missing_backlogged)} referenced verifier(s) still missing but "
                "backlogged: " + ", ".join(sorted(missing_backlogged)),
            )
        )
    if scorecard_only_orphans:
        results.append(
            _make_result(
                "WARN",
                "pack.scorecard-verifier-drift",
                "scorecard requires verifier(s) absent from the backlog "
                "implementation order (advisory): " + ", ".join(sorted(scorecard_only_orphans)),
            )
        )

    # (c, runtime-side) scripts/*.py references checked under runtime_root().
    script_refs: set[str] = set()
    for payload in payloads.values():
        script_refs |= collect_script_refs(payload)
    if not runtime_root_dir.exists():
        results.append(
            _make_result(
                "WARN",
                "pack.runtime-scripts",
                f"runtime root {runtime_root_dir} not present; skipped {len(script_refs)} "
                "script reference(s)",
            )
        )
    else:
        absent_scripts = sorted(
            s for s in script_refs if not (runtime_root_dir / s).exists()
        )
        if absent_scripts:
            results.append(
                _make_result(
                    "WARN",
                    "pack.runtime-scripts",
                    "runtime script reference(s) absent under runtime root: "
                    + ", ".join(absent_scripts),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "pack.runtime-scripts",
                    f"all {len(script_refs)} referenced runtime scripts present",
                )
            )

    # (d) report and backlog must exist.
    doc_missing = []
    if not report_path.is_file():
        doc_missing.append(report_path.name)
    if not backlog_exists:
        doc_missing.append(backlog_path.name)
    if doc_missing:
        results.append(
            _make_result(
                "FAIL",
                "pack.docs-present",
                "missing pack document(s): " + ", ".join(doc_missing),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "pack.docs-present",
                "backlog and hardening report both present",
            )
        )

    # (e) STATUS AUDIT staleness: if it claims no verifiers exist but some do.
    if backlog_exists and status_audit_claims_none_exist(backlog_text):
        landed = sorted(
            v for v in backlog_verifiers if (repo_root / "execution" / v).is_file()
        )
        if landed:
            results.append(
                _make_result(
                    "WARN",
                    "pack.status-audit-stale",
                    "backlog status audit is stale — update it; verifier(s) now exist: "
                    + ", ".join(landed),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "pack.status-audit",
                    "backlog status audit still matches disk (no listed verifier exists)",
                )
            )
    else:
        results.append(
            _make_result(
                "OK",
                "pack.status-audit",
                "backlog makes no unqualified 'no verifiers exist' claim",
            )
        )

    return results


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
