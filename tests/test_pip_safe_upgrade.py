"""Eval for the deterministic pip-safe-upgrade mechanism (bin/pip_safe_upgrade.py).

This IS the eval the LLM /pip-safe-upgrade skill never had: fixtures map an input
audit + dry-run output to the expected apply/block/skip decisions, plus an
idempotency check (same input -> same plan; already-upgraded state -> no-op).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.pip_safe_upgrade import (  # type: ignore[import-not-found]
    Candidate,
    decide,
    detect_downgrades,
    detect_ml_mode,
    parse_dry_run,
    run_plan,
    triage,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _outdated(name: str, version: str = "1.0.0", latest: str = "2.0.0") -> dict:
    return {"name": name, "version": version, "latest": latest}


def _cve(package: str, version: str = "1.0.0", vuln_ids: list[str] | None = None) -> dict:
    return {"package": package, "version": version, "vuln_ids": vuln_ids or ["GHSA-x"], "vuln_count": 1}


def _audit(outdated: list[dict] | None = None, cves: list[dict] | None = None) -> dict:
    return {
        "generated_at": "2026-06-14T00:00:00",
        "pip_outdated": outdated or [],
        "pip_cves": cves or [],
        "npm_audits": [],
    }


def _candidate(name: str, current: str = "1.0.0", target: str = "2.0.0", tier: str = "rest") -> Candidate:
    return Candidate(name=name, current=current, target=target, tier=tier)


def _clean_dry_run(name: str = "pkg", version: str = "2.0.0") -> str:
    return f"Would install {name}-{version}"


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------

class TriageTests(unittest.TestCase):

    def test_returns_empty_plan_for_empty_audit(self) -> None:
        self.assertEqual(triage(_audit()), [])

    def test_orders_cve_before_security_before_rest(self) -> None:
        audit = _audit(
            outdated=[_outdated("flask"), _outdated("certifi"), _outdated("torch")],
            cves=[_cve("torch")],
        )
        tiers = [c.tier for c in triage(audit)]
        self.assertEqual(tiers, ["cve", "security", "rest"])

    def test_lists_each_package_once_when_both_cve_and_outdated(self) -> None:
        audit = _audit(outdated=[_outdated("torch")], cves=[_cve("torch")])
        plan = triage(audit)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].tier, "cve")

    def test_includes_cve_package_absent_from_outdated(self) -> None:
        audit = _audit(outdated=[], cves=[_cve("requests")])
        plan = triage(audit)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0].name, "requests")
        self.assertEqual(plan[0].target, "latest")


# ---------------------------------------------------------------------------
# detect_ml_mode
# ---------------------------------------------------------------------------

class DetectMlModeTests(unittest.TestCase):

    def test_true_when_torch_installed(self) -> None:
        self.assertTrue(detect_ml_mode({"torch", "numpy"}))

    def test_false_when_no_ml_markers_installed(self) -> None:
        self.assertFalse(detect_ml_mode({"flask", "requests"}))


# ---------------------------------------------------------------------------
# parse_dry_run / detect_downgrades
# ---------------------------------------------------------------------------

class ParseDryRunTests(unittest.TestCase):

    def test_extracts_would_install_package_and_version(self) -> None:
        parsed = parse_dry_run("Would install certifi-2026.1.1")
        self.assertEqual(parsed["would_install"], [("certifi", "2026.1.1")])

    def test_flags_incompatible_output(self) -> None:
        parsed = parse_dry_run("torch 2.12.0 has requirement setuptools<82, but incompatible")
        self.assertTrue(parsed["incompatible"])

    def test_detects_same_package_downgrade(self) -> None:
        parsed = {
            "would_install": [("setuptools", "65.5.0")],
            "would_uninstall": [("setuptools", "81.0.0")],
            "incompatible": False,
        }
        self.assertEqual(detect_downgrades(parsed), [("setuptools", "81.0.0", "65.5.0")])

    def test_ignores_plain_upgrade_as_non_downgrade(self) -> None:
        parsed = {
            "would_install": [("certifi", "2026.1.1")],
            "would_uninstall": [("certifi", "2025.1.1")],
            "incompatible": False,
        }
        self.assertEqual(detect_downgrades(parsed), [])


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------

class DecideTests(unittest.TestCase):

    def test_skips_when_already_at_target(self) -> None:
        cand = _candidate("flask", current="3.0.0", target="3.0.0")
        action, _ = decide(cand, parse_dry_run(_clean_dry_run()), ml_mode=False)
        self.assertEqual(action, "skip")

    def test_blocks_torch_upgrade_in_ml_mode(self) -> None:
        action, reason = decide(_candidate("torch", target="2.13.0"), None, ml_mode=True)
        self.assertEqual(action, "block")
        self.assertIn("torch", reason)

    def test_blocks_setuptools_above_ceiling_in_ml_mode(self) -> None:
        action, _ = decide(_candidate("setuptools", current="81.0.0", target="82.1.0"), None, ml_mode=True)
        self.assertEqual(action, "block")

    def test_allows_setuptools_below_ceiling_in_ml_mode(self) -> None:
        cand = _candidate("setuptools", current="80.0.0", target="81.9.0")
        action, _ = decide(cand, parse_dry_run("Would install setuptools-81.9.0"), ml_mode=True)
        self.assertEqual(action, "apply")

    def test_blocks_on_incompatible_dry_run(self) -> None:
        parsed = parse_dry_run("error: incompatible dependency")
        action, _ = decide(_candidate("flask"), parsed, ml_mode=False)
        self.assertEqual(action, "block")

    def test_blocks_on_constraint_downgrade(self) -> None:
        parsed = {
            "would_install": [("setuptools", "65.5.0")],
            "would_uninstall": [("setuptools", "81.0.0")],
            "incompatible": False,
        }
        action, reason = decide(_candidate("setuptools", current="81.0.0", target="82.0.0"), parsed, ml_mode=False)
        self.assertEqual(action, "block")
        self.assertIn("downgrade", reason)

    def test_applies_on_clean_dry_run(self) -> None:
        action, _ = decide(_candidate("certifi"), parse_dry_run("Would install certifi-2026.1.1"), ml_mode=False)
        self.assertEqual(action, "apply")


# ---------------------------------------------------------------------------
# run_plan (end-to-end with injected fns — no pip side effects)
# ---------------------------------------------------------------------------

class RunPlanTests(unittest.TestCase):

    def test_applies_clean_cve_and_blocks_torch_in_ml_mode(self) -> None:
        audit = _audit(
            outdated=[_outdated("certifi", "2025.1.1", "2026.1.1"), _outdated("torch", "2.12.0", "2.13.0")],
            cves=[_cve("certifi"), _cve("torch")],
        )
        report = run_plan(
            audit,
            installed={"torch"},
            dry_run_fn=lambda name: f"Would install {name}-x",
        )
        applied_names = {r["name"] for r in report["applied"]}
        blocked_names = {r["name"] for r in report["blocked"]}
        self.assertEqual(applied_names, {"certifi"})
        self.assertEqual(blocked_names, {"torch"})

    def test_records_upgraded_flag_when_apply_requested(self) -> None:
        audit = _audit(outdated=[_outdated("certifi", "2025.1.1", "2026.1.1")])
        report = run_plan(
            audit,
            installed=set(),
            do_apply=True,
            dry_run_fn=lambda name: f"Would install {name}-x",
            apply_fn=lambda name: True,
        )
        self.assertTrue(report["applied"][0]["upgraded"])

    def test_does_not_call_apply_when_plan_only(self) -> None:
        calls: list[str] = []
        audit = _audit(outdated=[_outdated("certifi", "2025.1.1", "2026.1.1")])
        run_plan(
            audit,
            installed=set(),
            do_apply=False,
            dry_run_fn=lambda name: f"Would install {name}-x",
            apply_fn=lambda name: calls.append(name) or True,
        )
        self.assertEqual(calls, [])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def test_same_input_yields_identical_plan(self) -> None:
        audit = _audit(
            outdated=[_outdated("flask"), _outdated("certifi")],
            cves=[_cve("certifi")],
        )
        first = run_plan(audit, installed=set(), dry_run_fn=lambda name: f"Would install {name}-x")
        second = run_plan(audit, installed=set(), dry_run_fn=lambda name: f"Would install {name}-x")
        self.assertEqual(first, second)

    def test_no_op_when_everything_already_at_latest(self) -> None:
        audit = _audit(outdated=[_outdated("flask", "2.0.0", "2.0.0"), _outdated("certifi", "2026.1.1", "2026.1.1")])
        calls: list[str] = []
        report = run_plan(
            audit,
            installed=set(),
            do_apply=True,
            dry_run_fn=lambda name: calls.append(name) or "",
            apply_fn=lambda name: calls.append(name) or True,
        )
        self.assertEqual(report["counts"]["applied"], 0)
        self.assertEqual(report["counts"]["skipped"], 2)
        self.assertEqual(calls, [])  # already-upgraded packages trigger no pip calls


if __name__ == "__main__":
    unittest.main()
