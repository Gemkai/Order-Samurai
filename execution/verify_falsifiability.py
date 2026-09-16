"""Verifier_Falsifiability harness (2026-08-01 metric-gap remediation, phase C1).

A verifier that never sees a deliberately-bad input can silently CLEAN forever —
this month's audit found exactly that class of gap (silent-false-CLEAN instruments).
For every check registered below, this harness requires an explicit fixture pair:

    tests/falsifiability_fixtures/<check>/bad/     -- input the check MUST fail on
    tests/falsifiability_fixtures/<check>/clean/   -- input the check MUST pass on

Verifier_Falsifiability = (checks proven to fail-on-bad AND pass-on-clean)
                           / total registered checks.

A check with no fixture pair yet is UNTESTED — excluded from BOTH the numerator and
the denominator's covered set, never silently counted as passing (that would
recreate the exact gap this harness exists to close).

Initial coverage (phase C1's four instrument classes the audit found missing
falsifiability proof for, plus phase E2's own drift guard): NUL-byte content,
column-0 conflict markers, commit-range PII export (reuses the real
Order Samurai/bin/extract_public.py::verify_tree — the same PII-leak gate that
caught a live C:\\Users\\... leak past 28 green unit tests, 2026-07-12), doc
parity (wraps the real execution/verify_doc_parity.py::run_checks against a
fixture repo_root, so this exercises the shipped verifier itself, not a
re-implementation), and matrix<->registry drift (wraps
docs/regen_metrics_matrix.py::compute_drift — this harness is itself a verifier,
so it gets a fixture pair too).

Denominator note: the plan's "~20" is the execution/verify_*.py script count
(discovered by naming convention below); four of the five checks here have no
dedicated verify_*.py file of their own (no such content-integrity check existed
in the suite before this harness), so they are counted as ADDITIONAL registered
checks on top of the discovered script count, not as members of it. doc_parity
IS one of the discovered scripts (verify_doc_parity.py) and is not double-counted.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
_EXECUTION_DIR = _THIS.parent
_OS_ROOT = _EXECUTION_DIR.parent
_BIN_DIR = _OS_ROOT / "bin"
_DOCS_DIR = _OS_ROOT.parent / "docs"
for _p in (_EXECUTION_DIR, _OS_ROOT, _BIN_DIR, _DOCS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

FIXTURES_ROOT = _OS_ROOT / "tests" / "falsifiability_fixtures"

# Checks with no dedicated verify_*.py file — see module docstring's denominator note.
_CHECKS_WITHOUT_OWN_SCRIPT = {"nul_byte_content", "conflict_markers", "pii_export",
                              "matrix_registry_drift"}


def _scan_files(target_dir: Path):
    for p in sorted(target_dir.rglob("*")):
        if p.is_file():
            yield p


def check_nul_byte_content(target_dir: Path) -> tuple[bool, str]:
    """OK unless a file under target_dir contains a NUL byte — binary/corrupt
    content masquerading as text, which silently truncates or corrupts any
    downstream code that treats the file as a normal string."""
    for p in _scan_files(target_dir):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            return False, f"NUL byte in {p.relative_to(target_dir)}"
    return True, "no NUL bytes found"


# Column-0-anchored (MULTILINE ^) so prose that happens to mention "<<<<<<<" mid-line
# isn't a false positive — only an unresolved merge marker at the start of a line is real.
_CONFLICT_MARKER_RE = re.compile(r"^(<{7} .*|={7}|>{7} .*)$", re.MULTILINE)


def check_conflict_markers(target_dir: Path) -> tuple[bool, str]:
    """OK unless a file has a git conflict marker at column 0 — an unresolved
    merge left in committed content."""
    for p in _scan_files(target_dir):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = _CONFLICT_MARKER_RE.search(text)
        if m:
            return False, f"conflict marker {m.group(0)!r} in {p.relative_to(target_dir)}"
    return True, "no conflict markers found"


def check_pii_export(target_dir: Path) -> tuple[bool, str]:
    """OK unless extract_public.verify_tree finds a real PII/home-path identifier
    under target_dir. Reuses the live PII-leak gate directly (DRY) instead of a
    parallel regex reimplementation."""
    import extract_public  # noqa: PLC0415 (sys.path wired above)
    findings = extract_public.verify_tree(target_dir)
    if findings:
        rel, lineno, ident = findings[0]
        return False, f"identifier {ident!r} in {rel}:{lineno} ({len(findings)} total)"
    return True, "no PII identifiers found"


def check_doc_parity(target_dir: Path) -> tuple[bool, str]:
    """Wraps the real execution/verify_doc_parity.py::run_checks against a fixture
    repo_root. NOTE: run_checks also reads the live ANTI_DRIFT_POLICY_PATH (not
    parameterized) for the *set* of required docs — currently PROJECT.md and
    RONIN_SPEC.md — so these fixtures are coupled to that policy's declared
    expectedArtifacts; update them together if that list changes."""
    import verify_doc_parity  # noqa: PLC0415 (sys.path wired above)
    results = verify_doc_parity.run_checks(repo_root=target_dir)
    fails = [r for r in results if r["status"] == "FAIL"]
    if fails:
        return False, "; ".join(f"{r['label']}: {r['detail']}" for r in fails)
    return True, "no FAIL rows"


def check_matrix_registry_drift(target_dir: Path) -> tuple[bool, str]:
    """OK unless the metrics_remediation_matrix.md fixture under target_dir has
    drifted from the LIVE registry (docs/regen_metrics_matrix.py::compute_drift
    against the live dashboard payload -- see that module for the ground-truth
    source). This check is itself the E2 drift guard: the matrix roster is a
    verifier of the registry, so it gets a fixture pair like any other check.

    NOTE: like check_doc_parity, this fixture is coupled to today's live
    registry -- the 'clean' fixture is a snapshot of an accurate matrix at
    fixture-authoring time (2026-08-01) and would need refreshing (copy the
    regenerated docs/metrics_remediation_matrix.md over it) if the live metric
    set changes enough to make that snapshot stale. This mirrors the same
    limitation already accepted for check_doc_parity."""
    try:
        import regen_metrics_matrix as rmm  # noqa: PLC0415 (sys.path wired above)
        matrix_file = target_dir / "metrics_remediation_matrix.md"
        if not matrix_file.is_file():
            return False, "no metrics_remediation_matrix.md fixture found"
        payload_path = rmm._PAYLOAD_PATH
        if not payload_path.is_file():
            # Fail closed, not open -- the same guard the standalone fallback below
            # carries. Until 2026-09-02 this branch let load_live_roster() raise
            # FileNotFoundError on any checkout without the gitignored payload
            # (every fresh clone and CI runner), so the whole harness died with a
            # traceback and no Summary line instead of reporting a parseable FAIL.
            return False, f"cannot verify: no live registry payload at {payload_path}"
        text = matrix_file.read_text(encoding="utf-8")
        live_roster = rmm.load_live_roster(payload_path)
        in_matrix_not_live, live_not_in_matrix = rmm.compute_drift(text, live_roster)
        if in_matrix_not_live or live_not_in_matrix:
            return False, f"in_matrix_not_live={in_matrix_not_live} live_not_in_matrix={live_not_in_matrix}"
        return True, "matrix matches live registry"
    except ImportError:
        # Standalone export fallback: regen_metrics_matrix itself can't be imported
        # here (its top-level `from agentica_core import insights` is unavailable in
        # a standalone distribution) even though the drift logic it wraps needs
        # nothing but stdlib -- load_live_roster/compute_drift are reimplemented
        # locally below (_standalone_load_live_roster/_standalone_compute_drift) so
        # this stays a REAL content-based check. (Prior version matched on literal
        # strings like "FakeMetric"/"NON_EXISTENT_METRIC" or the fixture directory
        # being named "bad" -- the actual bad/ fixture doesn't even contain those
        # substrings, so it only "passed" via the directory-name shortcut; any real
        # standalone-export drift not spelled exactly that way silently reported
        # clean. Found + fixed 2026-08-31, see docs/solutions.)
        matrix_file = target_dir / "metrics_remediation_matrix.md"
        if not matrix_file.is_file():
            return False, "no metrics_remediation_matrix.md fixture found"
        payload_path = _OS_ROOT.parent / "dashboard-ui" / "public" / "wid_payload.json"
        if not payload_path.is_file():
            # Fail closed, not open: a verifier that cannot see ground truth must
            # never silently report clean -- this file's own governing principle
            # (see module docstring).
            return False, f"cannot verify: no live registry payload at {payload_path}"
        text = matrix_file.read_text(encoding="utf-8")
        live_roster = _standalone_load_live_roster(payload_path)
        in_matrix_not_live, live_not_in_matrix = _standalone_compute_drift(text, live_roster)
        if in_matrix_not_live or live_not_in_matrix:
            return False, f"in_matrix_not_live={in_matrix_not_live} live_not_in_matrix={live_not_in_matrix}"
        return True, "matrix matches live registry"


_PILLAR_ORDER = ("bow", "sword", "brush", "arts")


def _standalone_load_live_roster(payload_path: Path) -> dict:
    """Reimplements regen_metrics_matrix.load_live_roster() without importing that
    module -- see check_matrix_registry_drift's except-ImportError branch for why.
    Keep in sync with that function if the payload shape ever changes."""
    import json
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    return payload.get("pillars", {})


def _standalone_compute_drift(matrix_text: str, live_roster: dict) -> tuple[list[str], list[str]]:
    """Reimplements regen_metrics_matrix.compute_drift() without importing that
    module -- see check_matrix_registry_drift's except-ImportError branch for why.
    Keep in sync with that function if the marker format or roster shape changes."""
    in_matrix: set[str] = set()
    for pillar in _PILLAR_ORDER:
        start = f"<!-- GENERATED:ROSTER:{pillar.upper()}:START -->"
        end = f"<!-- GENERATED:ROSTER:{pillar.upper()}:END -->"
        m = re.search(re.escape(start) + r"(.*?)" + re.escape(end), matrix_text, re.DOTALL)
        if not m:
            continue
        in_matrix.update(re.findall(r"\*\*([A-Za-z0-9_]+)\*\*", m.group(1)))

    live: set[str] = set()
    for pillar in _PILLAR_ORDER:
        for group_metrics in live_roster.get(pillar, {}).values():
            live.update(group_metrics)

    return sorted(in_matrix - live), sorted(live - in_matrix)


CHECKS = {
    "nul_byte_content": check_nul_byte_content,
    "conflict_markers": check_conflict_markers,
    "pii_export": check_pii_export,
    "doc_parity": check_doc_parity,
    "matrix_registry_drift": check_matrix_registry_drift,
    "incident_coverage": lambda target_dir: _check_incident_coverage(target_dir),
}


def _check_incident_coverage(target_dir: Path) -> tuple[bool, str]:
    """Run the real incident verifier against one covered/uncovered fixture doc."""
    import verify_incident_coverage
    report = verify_incident_coverage.analyze(
        solution_roots=(target_dir,),
        memory_root=target_dir,
    )
    ok = not report["uncovered"] and not report["unmeasured"]
    return ok, (
        f"{report['covered']}/{report['scanned']} covered; "
        f"{len(report['uncovered'])} uncovered"
    )


def discover_verify_scripts() -> list[str]:
    """Every execution/verify_*.py script by naming convention — the harness's
    full scope (~20+), independent of which ones have fixtures yet."""
    return sorted(p.stem for p in _EXECUTION_DIR.glob("verify_*.py"))


def run_falsifiability(checks: dict | None = None, fixtures_root: Path | None = None,
                       verify_scripts: list[str] | None = None) -> dict:
    checks = checks if checks is not None else CHECKS
    fixtures_root = fixtures_root if fixtures_root is not None else FIXTURES_ROOT
    verify_scripts = verify_scripts if verify_scripts is not None else discover_verify_scripts()

    results: dict[str, dict] = {}
    for name, fn in checks.items():
        bad_dir = fixtures_root / name / "bad"
        clean_dir = fixtures_root / name / "clean"
        if not bad_dir.is_dir() or not clean_dir.is_dir():
            results[name] = {"status": "untested", "detail": "no fixture pair"}
            continue
        bad_ok, bad_detail = fn(bad_dir)
        clean_ok, clean_detail = fn(clean_dir)
        # The bad fixture must FAIL the check (ok is False); the clean fixture must PASS (ok is True).
        falsifiable = (bad_ok is False) and (clean_ok is True)
        results[name] = {
            "status": "pass" if falsifiable else "fail",
            "bad_fixture": {"ok": bad_ok, "detail": bad_detail},
            "clean_fixture": {"ok": clean_ok, "detail": clean_detail},
        }

    tested = {k: v for k, v in results.items() if v["status"] != "untested"}
    falsifiable_count = sum(1 for v in tested.values() if v["status"] == "pass")
    denominator = len(verify_scripts) + sum(
        1 for name in checks if name in _CHECKS_WITHOUT_OWN_SCRIPT
    )
    return {
        "falsifiable": falsifiable_count,
        "total": denominator,
        "checks": results,
        "verify_scripts_discovered": len(verify_scripts),
        "note": ("Verifier_Falsifiability = checks proven to fail-on-bad AND pass-on-clean, "
                 "over total registered checks (discovered verify_*.py scripts plus checks "
                 "with no dedicated script of their own); an untested check counts in neither "
                 "the numerator nor the denominator's covered set — see 'checks' for detail."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Verifier_Falsifiability harness")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON (the full run_falsifiability() "
                             "result) instead of the human-readable summary")
    args = parser.parse_args()

    r = run_falsifiability()

    if args.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"Verifier_Falsifiability: {r['falsifiable']}/{r['total']} checks proven falsifiable "
              f"(bad fixture fails, clean fixture passes)")
        for name, v in sorted(r["checks"].items()):
            print(f"  {name}: {v['status']}")

    # A registered-but-untested check is a coverage gap, not a failure (this file's own
    # docstring: "an untested check counts in neither the numerator nor the denominator's
    # covered set") -- `falsifiable == total` would demand full coverage of EVERY
    # discovered verify_*.py script (most of which have no fixture pair registered at
    # all yet) and would exit nonzero on every run regardless of real health. The real
    # signal is narrower: did any REGISTERED check fail to prove itself falsifiable.
    # (Found + fixed 2026-08-31/09-01: this previously always returned 0 regardless of
    # results, so callers checking the exit code alone -- e.g. reconcile_state.py's
    # stage_3 -- could never see a real failure here.)
    any_check_failed = any(v["status"] == "fail" for v in r["checks"].values())
    return 1 if any_check_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
