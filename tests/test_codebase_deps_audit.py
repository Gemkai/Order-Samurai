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
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.codebase_deps_audit import (  # type: ignore[import-not-found]
    build_audit,
    classify_licence,
    parse_npm_audit,
    parse_pip_audit,
    parse_pip_outdated,
    run_audit,
    scan_npm_projects,
    scan_licences,
    write_audit,
)
from bin import codebase_deps_audit as deps_audit  # type: ignore[import-not-found]

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


def _npm_audit_json(total: int = 0) -> str:
    return json.dumps({
        "auditReportVersion": 2,
        "vulnerabilities": ({
            "example": {
                "name": "example",
                "severity": "high",
                "via": [{"source": 123, "url": "https://github.com/advisories/GHSA-test"}],
            }
        } if total else {}),
        "metadata": {
            "vulnerabilities": {
                "info": 0, "low": 0, "moderate": 0,
                "high": total, "critical": 0, "total": total,
            }
        },
    })


def _healthy_npm_result(*audits: dict) -> dict:
    return {
        "audits": list(audits),
        "scanner_ok": True,
        "projects": {"fixture": True},
        "errors": {},
    }


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


class ParseNpmAuditTests(unittest.TestCase):

    def test_extracts_total_breakdown_and_stable_advisory_id(self) -> None:
        parsed = parse_npm_audit(_npm_audit_json(1), "Governance/api")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["project"], "Governance/api")
        self.assertEqual(parsed["total"], 1)
        self.assertEqual(parsed["findings"][0]["vuln_ids"], ["GHSA-test"])

    def test_rejects_error_document_instead_of_returning_zero(self) -> None:
        raw = json.dumps({"error": {"summary": "registry unavailable"}})
        self.assertIsNone(parse_npm_audit(raw, "Governance"))

    def test_rejects_json_without_vulnerability_metadata(self) -> None:
        self.assertIsNone(parse_npm_audit("{}", "Governance"))


class ScanNpmProjectsTests(unittest.TestCase):

    def test_exit_one_with_valid_json_is_a_successful_vulnerability_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            result = scan_npm_projects(
                [("fixture", root)],
                npm_executable="/fixture/npm",
                run_fn=lambda *args, **kwargs: SimpleNamespace(
                    returncode=1, stdout=_npm_audit_json(1), stderr=""
                ),
            )
        self.assertTrue(result["scanner_ok"])
        self.assertEqual(result["audits"][0]["total"], 1)

    def test_missing_lockfile_is_an_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_npm_projects(
                [("fixture", Path(tmp))],
                npm_executable="/fixture/npm",
                run_fn=lambda *args, **kwargs: self.fail("runner must not be called"),
            )
        self.assertFalse(result["scanner_ok"])
        self.assertEqual(result["projects"], {"fixture": False})
        self.assertIn("package-lock", result["errors"]["fixture"])

    def test_malformed_json_is_failure_not_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package-lock.json").write_text("{}", encoding="utf-8")
            result = scan_npm_projects(
                [("fixture", root)],
                npm_executable="/fixture/npm",
                run_fn=lambda *args, **kwargs: SimpleNamespace(
                    returncode=0, stdout="not-json", stderr=""
                ),
            )
        self.assertFalse(result["scanner_ok"])
        self.assertEqual(result["audits"], [])


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

    def test_clears_spdx_or_expression_when_all_operands_permissive(self) -> None:
        self.assertEqual(classify_licence("Apache-2.0 OR BSD-3-Clause"), "permissive")

    def test_clears_spdx_and_expression_when_all_operands_permissive(self) -> None:
        self.assertEqual(classify_licence("MIT AND PSF-2.0"), "permissive")

    def test_flags_spdx_expression_with_copyleft_operand_as_copyleft(self) -> None:
        self.assertEqual(classify_licence("MIT OR GPL-2.0"), "copyleft")

    def test_flags_spdx_expression_with_unknown_operand_as_unknown(self) -> None:
        self.assertEqual(classify_licence("MIT AND Proprietary"), "unknown")

    def test_clears_pep639_spdx_ids(self) -> None:
        for expr in ("PSF-2.0", "MIT-0", "CNRI-Python", "0BSD"):
            self.assertEqual(classify_licence(expr), "permissive", expr)

    def test_clears_prose_bsd_variants_via_any_token(self) -> None:
        self.assertEqual(classify_licence("Modified BSD License"), "permissive")
        self.assertEqual(classify_licence("3-Clause BSD License"), "permissive")


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
            npm_audit_fn=lambda: _healthy_npm_result(),
            now_fn=lambda: FROZEN_TS,
        )
        defaults.update(overrides)
        return run_audit(**defaults)

    def test_assembles_findings_from_all_scanners(self) -> None:
        audit = self._run()
        self.assertEqual(audit["counts"], {
            "outdated": 1, "cves": 1, "pip_cves": 1, "npm_cves": 0,
            "licence_flags": 1, "needs_review": 2,
        })

    def test_counts_include_npm_vulnerabilities(self) -> None:
        npm = parse_npm_audit(_npm_audit_json(1), "Governance/api")
        assert npm is not None
        audit = self._run(npm_audit_fn=lambda: _healthy_npm_result(npm))
        self.assertEqual(audit["counts"]["pip_cves"], 1)
        self.assertEqual(audit["counts"]["npm_cves"], 1)
        self.assertEqual(audit["counts"]["cves"], 2)

    def test_skips_licence_scan_when_disabled(self) -> None:
        called: list[str] = []
        audit = self._run(
            include_licences=False,
            licence_fn=lambda: called.append("licence") or [],
        )
        self.assertEqual(audit["counts"]["licence_flags"], 0)
        self.assertEqual(called, [])  # disabled scan is never invoked

    def test_records_clean_npm_scan_without_findings(self) -> None:
        self.assertEqual(self._run()["npm_audits"], [])

    def test_all_scanners_have_explicit_healthy_verdicts(self) -> None:
        self.assertEqual(self._run()["scanner_ok"], {
            "pip": True, "pip_audit": True, "npm": True,
        })

    def test_malformed_python_output_is_failure_not_clean(self) -> None:
        audit = self._run(pip_outdated_fn=lambda: "not-json", pip_audit_fn=lambda: "{}")
        self.assertFalse(audit["scanner_ok"]["pip"])
        self.assertFalse(audit["scanner_ok"]["pip_audit"])
        self.assertIn("pip", audit["scanner_errors"])
        self.assertIn("pip_audit", audit["scanner_errors"])

    def test_missing_pip_audit_writes_incomplete_artifact_and_exits_nonzero(self) -> None:
        audit = self._run(pip_audit_fn=lambda: None)
        self.assertFalse(audit["scanner_ok"]["pip_audit"])
        self.assertEqual(audit["counts"]["pip_cves"], 0)
        self.assertIn("pip-audit", audit["scanner_errors"]["pip_audit"])

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dependency_audit.json"
            with mock.patch.object(deps_audit, "run_audit", return_value=audit):
                exit_code = deps_audit.main(["--out", str(out), "--json"])
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 2)
        self.assertFalse(written["scanner_ok"]["pip_audit"])

    def test_npm_scan_skipped_by_default_is_not_a_scanner_failure(self) -> None:
        audit = self._run(npm_audit_fn=None)
        self.assertEqual(audit["npm_audits"], [])
        self.assertNotIn("npm", audit["scanner_ok"])
        self.assertNotIn("npm", audit["scanner_errors"])
        self.assertNotIn("npm", audit["scanner_details"])
        self.assertTrue(all(audit["scanner_ok"].values()))

    def test_cli_runs_npm_scan_only_with_the_npm_flag(self) -> None:
        audit = self._run(npm_audit_fn=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dependency_audit.json"
            with mock.patch.object(deps_audit, "run_audit", return_value=audit) as run:
                self.assertEqual(deps_audit.main(["--out", str(out), "--json"]), 0)
                self.assertIsNone(run.call_args.kwargs["npm_audit_fn"])
                deps_audit.main(["--out", str(out), "--json", "--npm"])
                self.assertIs(run.call_args.kwargs["npm_audit_fn"],
                              deps_audit._real_npm_audits)

    def test_failed_npm_scan_remains_explicit(self) -> None:
        audit = self._run(npm_audit_fn=lambda: {
            "audits": [], "scanner_ok": False,
            "projects": {"Governance/api": False},
            "errors": {"Governance/api": "registry unavailable"},
        })
        self.assertFalse(audit["scanner_ok"]["npm"])
        self.assertEqual(
            audit["scanner_errors"]["npm"]["Governance/api"],
            "registry unavailable",
        )

    def test_cli_returns_nonzero_after_writing_failed_scan_artifact(self) -> None:
        audit = self._run(npm_audit_fn=lambda: {
            "audits": [], "scanner_ok": False,
            "projects": {"Governance": False},
            "errors": {"Governance": "registry unavailable"},
        })
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dependency_audit.json"
            with mock.patch.object(deps_audit, "run_audit", return_value=audit):
                exit_code = deps_audit.main(["--out", str(out), "--json"])
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 2)
        self.assertFalse(written["scanner_ok"]["npm"])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def _stable_run(self) -> dict:
        return run_audit(
            pip_outdated_fn=lambda: _pip_outdated_json(("flask", "2.0.0", "3.0.0")),
            pip_audit_fn=lambda: _pip_audit_json(("requests", "2.0.0", ["CVE-1"])),
            licence_fn=lambda: [("paramiko", "3.0.0", "GPL-3.0")],
            npm_audit_fn=lambda: _healthy_npm_result(),
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
            npm_audit_fn=lambda: calls.append("npm") or _healthy_npm_result(),
            now_fn=lambda: FROZEN_TS,
        )
        self.assertEqual(sorted(calls), ["audit", "licence", "npm", "outdated"])


class GovernanceRootResolutionTests(unittest.TestCase):
    """Root resolution must hold in BOTH supported layouts (see tests/_layout.py):
    nested live repo (Governance/Order Samurai/bin/…) and the flat product pack
    (<pack>/bin/…), where a bare parents[2] walks out of the pack entirely."""

    def _resolve(self, script_path: Path) -> Path:
        with mock.patch.dict(deps_audit.os.environ, clear=False):
            deps_audit.os.environ.pop("GOVERNANCE_ROOT", None)
            return deps_audit._resolve_governance_root(script_path)

    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "custom-root"
            override.mkdir()
            with mock.patch.dict(deps_audit.os.environ,
                                 {"GOVERNANCE_ROOT": str(override)}):
                got = deps_audit._resolve_governance_root(Path(td) / "bin" / "x.py")
        self.assertEqual(got, override.resolve())

    def test_nested_repo_layout_resolves_governance_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gov = Path(td) / "Governance"
            (gov / "agentica_core").mkdir(parents=True)
            script = gov / "Order Samurai" / "bin" / "codebase_deps_audit.py"
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            self.assertEqual(self._resolve(script), gov.resolve())

    def test_flat_product_pack_stays_inside_the_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pack = Path(td) / "Order Samurai(product)"
            (pack / "agentica_core").mkdir(parents=True)
            script = pack / "bin" / "codebase_deps_audit.py"
            script.parent.mkdir(parents=True)
            script.write_text("", encoding="utf-8")
            got = self._resolve(script)
        self.assertEqual(got, pack.resolve())
        self.assertNotEqual(got, Path(td).resolve())  # must NOT escape the pack

    def test_live_module_root_contains_agentica_core(self) -> None:
        self.assertTrue((deps_audit.GOVERNANCE_ROOT / "agentica_core").is_dir())


if __name__ == "__main__":
    unittest.main()
