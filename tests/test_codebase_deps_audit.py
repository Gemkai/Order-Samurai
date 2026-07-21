"""Eval for the deterministic codebase-cleanup-deps-audit mechanism
(bin/codebase_deps_audit.py).

This IS the eval the LLM /codebase-cleanup-deps-audit skill never had: fixtures map
raw scanner output to the expected parsed findings and assembled audit, plus an
idempotency check (same scanner output -> identical audit; the scan never mutates a
dependency, so re-running is a no-op). All scanners are injected — no test ever
shells out, and the audit produced feeds straight into pip_safe_upgrade's contract.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.codebase_deps_audit import (  # type: ignore[import-not-found]
    build_audit,
    classify_licence,
    parse_pip_audit,
    parse_pip_outdated,
    run_audit,
    scan_licences,
    write_audit,
)

# Cross-mechanism contract: the audit this produces must be readable by
# pip_safe_upgrade.triage(), the downstream consumer.
from bin.pip_safe_upgrade import triage  # type: ignore[import-not-found]


FROZEN_TS = "2026-06-15T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _pip_outdated_json(*rows: tuple[str, str, str]) -> str:
    """Raw `pip list --outdated --format json` stdout for (name, version, latest)."""
    return json.dumps(
        [{"name": n, "version": v, "latest_version": latest} for n, v, latest in rows]
    )


def _pip_audit_json(*deps: tuple[str, str, list[str]]) -> str:
    """Raw `pip-audit --format json` stdout (envelope shape) for (name, version, ids)."""
    return json.dumps(
        {
            "dependencies": [
                {
                    "name": n,
                    "version": v,
                    "vulns": [{"id": i} for i in ids],
                }
                for n, v, ids in deps
            ]
        }
    )


# ---------------------------------------------------------------------------
# parse_pip_outdated
# ---------------------------------------------------------------------------

class ParsePipOutdatedTests(unittest.TestCase):

    def test_maps_latest_version_to_latest_field(self) -> None:
        parsed = parse_pip_outdated(_pip_outdated_json(("flask", "2.0.0", "3.0.0")))
        self.assertEqual(parsed, [{"name": "flask", "version": "2.0.0", "latest": "3.0.0"}])

    def test_returns_empty_list_for_empty_output(self) -> None:
        self.assertEqual(parse_pip_outdated(""), [])

    def test_returns_empty_list_for_malformed_json(self) -> None:
        self.assertEqual(parse_pip_outdated("not json"), [])

    def test_sorts_packages_alphabetically(self) -> None:
        parsed = parse_pip_outdated(
            _pip_outdated_json(("zstandard", "1.0", "2.0"), ("attrs", "1.0", "2.0"))
        )
        self.assertEqual([p["name"] for p in parsed], ["attrs", "zstandard"])


# ---------------------------------------------------------------------------
# parse_pip_audit
# ---------------------------------------------------------------------------

class ParsePipAuditTests(unittest.TestCase):

    def test_extracts_vuln_ids_and_count(self) -> None:
        parsed = parse_pip_audit(_pip_audit_json(("requests", "2.0.0", ["GHSA-a", "CVE-1"])))
        self.assertEqual(
            parsed,
            [{"package": "requests", "version": "2.0.0",
              "vuln_ids": ["CVE-1", "GHSA-a"], "vuln_count": 2}],
        )

    def test_omits_dependencies_with_no_vulns(self) -> None:
        self.assertEqual(parse_pip_audit(_pip_audit_json(("safe-pkg", "1.0.0", []))), [])

    def test_accepts_older_top_level_list_shape(self) -> None:
        raw = json.dumps([{"name": "urllib3", "version": "1.0", "vulns": [{"id": "CVE-9"}]}])
        parsed = parse_pip_audit(raw)
        self.assertEqual(parsed[0]["package"], "urllib3")

    def test_returns_empty_list_for_empty_output(self) -> None:
        self.assertEqual(parse_pip_audit(""), [])


# ---------------------------------------------------------------------------
# classify_licence / scan_licences
# ---------------------------------------------------------------------------

class ClassifyLicenceTests(unittest.TestCase):

    def test_clears_mit_as_permissive(self) -> None:
        self.assertEqual(classify_licence("MIT License"), "permissive")

    def test_flags_gpl_as_copyleft(self) -> None:
        self.assertEqual(classify_licence("GPL-3.0"), "copyleft")

    def test_flags_empty_licence_as_unknown(self) -> None:
        self.assertEqual(classify_licence(""), "unknown")

    def test_flags_proprietary_string_as_unknown(self) -> None:
        self.assertEqual(classify_licence("Proprietary"), "unknown")


class ScanLicencesTests(unittest.TestCase):

    def test_omits_permissive_packages(self) -> None:
        flags = scan_licences([("flask", "3.0.0", "BSD-3-Clause")])
        self.assertEqual(flags, [])

    def test_flags_copyleft_package(self) -> None:
        flags = scan_licences([("paramiko", "3.0.0", "LGPL")])
        self.assertEqual(flags[0], {"name": "paramiko", "version": "3.0.0",
                                    "licence": "LGPL", "flag": "copyleft"})

    def test_defaults_missing_licence_string_to_unknown_label(self) -> None:
        flags = scan_licences([("mystery", "1.0.0", None)])
        self.assertEqual(flags[0]["licence"], "UNKNOWN")
        self.assertEqual(flags[0]["flag"], "unknown")


# ---------------------------------------------------------------------------
# build_audit — assembly + findings/action split
# ---------------------------------------------------------------------------

class BuildAuditTests(unittest.TestCase):

    def test_routes_cves_and_copyleft_into_needs_review(self) -> None:
        audit = build_audit(
            pip_outdated=[{"name": "flask", "version": "2.0.0", "latest": "3.0.0"}],
            pip_cves=[{"package": "requests", "version": "2.0.0",
                       "vuln_ids": ["CVE-1"], "vuln_count": 1}],
            licence_flags=[{"name": "paramiko", "version": "3.0.0",
                            "licence": "LGPL", "flag": "copyleft"}],
            generated_at=FROZEN_TS,
        )
        self.assertEqual(audit["counts"]["needs_review"], 2)
        self.assertEqual(len(audit["needs_review"]["licences"]), 1)
        self.assertEqual(len(audit["needs_review"]["cves"]), 1)

    def test_produces_dict_consumable_by_pip_safe_upgrade_triage(self) -> None:
        audit = build_audit(
            pip_outdated=[{"name": "certifi", "version": "2025.1.1", "latest": "2026.1.1"}],
            pip_cves=[{"package": "certifi", "version": "2025.1.1",
                       "vuln_ids": ["CVE-1"], "vuln_count": 1}],
            licence_flags=[],
            generated_at=FROZEN_TS,
        )
        plan = triage(audit)  # downstream consumer must accept our output as-is
        self.assertEqual(plan[0].name, "certifi")
        self.assertEqual(plan[0].tier, "cve")


# ---------------------------------------------------------------------------
# run_audit (end-to-end with injected scanners — no shell)
# ---------------------------------------------------------------------------

class RunAuditTests(unittest.TestCase):

    def _run(self, **overrides) -> dict:
        defaults = dict(
            pip_outdated_fn=lambda: _pip_outdated_json(("flask", "2.0.0", "3.0.0")),
            pip_audit_fn=lambda: _pip_audit_json(("requests", "2.0.0", ["CVE-1"])),
            licence_fn=lambda: [("paramiko", "3.0.0", "GPL-3.0")],
            now_fn=lambda: FROZEN_TS,
        )
        defaults.update(overrides)
        return run_audit(**defaults)

    def test_assembles_findings_from_all_scanners(self) -> None:
        audit = self._run()
        self.assertEqual(audit["counts"], {"outdated": 1, "cves": 1,
                                           "licence_flags": 1, "needs_review": 2})

    def test_skips_licence_scan_when_disabled(self) -> None:
        called: list[str] = []
        audit = self._run(
            include_licences=False,
            licence_fn=lambda: called.append("licence") or [],
        )
        self.assertEqual(audit["counts"]["licence_flags"], 0)
        self.assertEqual(called, [])  # disabled scan is never invoked

    def test_leaves_npm_audits_empty_without_an_npm_hook(self) -> None:
        self.assertEqual(self._run()["npm_audits"], [])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def _stable_run(self) -> dict:
        return run_audit(
            pip_outdated_fn=lambda: _pip_outdated_json(("flask", "2.0.0", "3.0.0")),
            pip_audit_fn=lambda: _pip_audit_json(("requests", "2.0.0", ["CVE-1"])),
            licence_fn=lambda: [("paramiko", "3.0.0", "GPL-3.0")],
            now_fn=lambda: FROZEN_TS,
        )

    def test_same_scanner_output_yields_identical_audit(self) -> None:
        self.assertEqual(self._stable_run(), self._stable_run())

    def test_writing_audit_twice_yields_identical_bytes(self) -> None:
        import tempfile

        audit = self._stable_run()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dependency_audit.json"
            write_audit(audit, path)
            first = path.read_bytes()
            write_audit(audit, path)
            second = path.read_bytes()
        self.assertEqual(first, second)

    def test_scanners_are_read_only_called_once_each(self) -> None:
        calls: list[str] = []
        run_audit(
            pip_outdated_fn=lambda: calls.append("outdated") or "[]",
            pip_audit_fn=lambda: calls.append("audit") or "[]",
            licence_fn=lambda: calls.append("licence") or [],
            now_fn=lambda: FROZEN_TS,
        )
        self.assertEqual(sorted(calls), ["audit", "licence", "outdated"])


if __name__ == "__main__":
    unittest.main()
