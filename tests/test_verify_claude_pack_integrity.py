from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.claude_runtime_target import (  # type: ignore[attr-defined]
    INTERNAL_ONLY_ARTIFACTS,
)
from execution.verify_claude_pack_integrity import (  # type: ignore[attr-defined]
    collect_inside_repo_artifacts,
    collect_verifier_refs,
    parse_backlog_verifiers,
    run_checks,
    status_audit_claims_none_exist,
    summarize,
)


def _status_by_label(results: list[dict]) -> dict[str, str]:
    return {r["label"]: r["status"] for r in results}


class PackIntegrityHelperTests(unittest.TestCase):
    def test_collect_verifier_refs_finds_nested_execution_paths(self) -> None:
        payload = {
            "categories": [
                {"requiredVerifiers": ["execution/verify_a.py"]},
                {"verifier": "execution/verify_b.py", "note": "scripts/x.py"},
            ]
        }

        self.assertEqual(
            collect_verifier_refs(payload),
            {"execution/verify_a.py", "execution/verify_b.py"},
        )

    def test_parse_backlog_verifiers_returns_basenames(self) -> None:
        text = "### 2. `execution/verify_a.py`\n\n2. `verify_a.py`\n3. `verify_b.py`\n"

        self.assertEqual(parse_backlog_verifiers(text), {"verify_a.py", "verify_b.py"})

    def test_status_audit_claims_none_exist_detects_claim(self) -> None:
        self.assertTrue(
            status_audit_claims_none_exist("none of the 14 verifiers below exist in code")
        )

    def test_status_audit_claims_none_exist_false_without_claim(self) -> None:
        self.assertFalse(status_audit_claims_none_exist("six verifiers already exist"))

    def test_collect_inside_repo_artifacts_filters_by_prefix(self) -> None:
        scorecard = {
            "categories": [
                {
                    "requiredPackArtifacts": ["config/p.json"],
                    "requiredRuntimeArtifacts": ["scripts/runtime.py", "execution/doctor.py"],
                }
            ]
        }

        self.assertEqual(
            collect_inside_repo_artifacts(scorecard),
            {"config/p.json", "execution/doctor.py"},
        )


class PackIntegrityRunChecksTests(unittest.TestCase):
    def _sandbox(self) -> Path:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_claude_pack_integrity" / self._testMethodName
        for sub in ("config", "backlog", "reports", "execution", "runtime"):
            (sandbox / sub).mkdir(parents=True, exist_ok=True)
        return sandbox

    def _build_pack(self, sandbox: Path, *, backlog_claims_none: bool = False) -> dict:
        scorecard_path = sandbox / "config" / "scorecard.json"
        policy_path = sandbox / "config" / "policy_a.json"
        backlog_path = sandbox / "backlog" / "backlog.md"
        # The REAL internal-only filename: the standalone exemption is keyed to the
        # specific document declared internal-only, so a synthetic name here would
        # never exercise it (and would let a broken exemption look tested).
        report_rel = INTERNAL_ONLY_ARTIFACTS[0]
        report_path = sandbox / report_rel

        scorecard_path.write_text(
            json.dumps(
                {
                    "categories": [
                        {
                            "requiredPackArtifacts": ["config/policy_a.json"],
                            "requiredRuntimeArtifacts": ["scripts/present.py"],
                            "requiredVerifiers": ["execution/verify_present.py"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        policy_path.write_text(
            json.dumps(
                {"rules": [{"id": "r", "verifier": "execution/verify_present.py"}]}
            ),
            encoding="utf-8",
        )
        audit = (
            "> STATUS AUDIT: none of the 3 verifiers below exist in `execution/`.\n\n"
            if backlog_claims_none
            else ""
        )
        backlog_path.write_text(
            audit + "## Implementation Order\n\n1. `verify_present.py`\n",
            encoding="utf-8",
        )
        report_path.write_text("# report\n", encoding="utf-8")
        (sandbox / "execution" / "verify_present.py").write_text("# stub\n", encoding="utf-8")
        (sandbox / "runtime" / "scripts").mkdir(parents=True, exist_ok=True)
        (sandbox / "runtime" / "scripts" / "present.py").write_text("# stub\n", encoding="utf-8")

        return {
            "policy_paths": (scorecard_path, policy_path),
            "scorecard_path": scorecard_path,
            "backlog_path": backlog_path,
            "report_path": report_path,
            "repo_root": sandbox,
            "runtime_root_dir": sandbox / "runtime",
        }

    def test_coherent_pack_produces_no_failures(self) -> None:
        kwargs = self._build_pack(self._sandbox())

        _, exit_code = summarize(run_checks(**kwargs))

        self.assertEqual(exit_code, 0)

    def test_scorecard_naming_missing_in_repo_artifact_fails(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        scorecard = json.loads(kwargs["scorecard_path"].read_text(encoding="utf-8"))
        scorecard["categories"][0]["requiredPackArtifacts"].append("config/does_not_exist.json")
        kwargs["scorecard_path"].write_text(json.dumps(scorecard), encoding="utf-8")

        status = _status_by_label(run_checks(**kwargs))

        self.assertEqual(status["pack.scorecard-artifacts"], "FAIL")

    def test_policy_naming_unbacklogged_missing_verifier_fails(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        policy = json.loads(kwargs["policy_paths"][1].read_text(encoding="utf-8"))
        policy["rules"].append({"id": "orphan", "verifier": "execution/verify_orphan.py"})
        kwargs["policy_paths"][1].write_text(json.dumps(policy), encoding="utf-8")

        status = _status_by_label(run_checks(**kwargs))

        self.assertEqual(status["pack.verifier-refs-orphaned"], "FAIL")

    def test_scorecard_only_unbacklogged_missing_verifier_warns_not_fails(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        scorecard = json.loads(kwargs["scorecard_path"].read_text(encoding="utf-8"))
        scorecard["categories"][0]["requiredVerifiers"].append("execution/verify_drift.py")
        kwargs["scorecard_path"].write_text(json.dumps(scorecard), encoding="utf-8")

        results = run_checks(**kwargs)
        status = _status_by_label(results)

        self.assertEqual(status["pack.scorecard-verifier-drift"], "WARN")
        self.assertEqual(summarize(results)[1], 0)

    def test_missing_backlogged_verifier_warns(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        (sandbox / "execution" / "verify_present.py").unlink()

        results = run_checks(**kwargs)
        status = _status_by_label(results)

        self.assertEqual(status["pack.verifier-refs-backlogged"], "WARN")
        self.assertEqual(summarize(results)[1], 0)

    def test_missing_report_fails_in_a_nested_checkout(self) -> None:
        """Where the report IS promised, deleting it is pack rot and must fail."""
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        kwargs["report_path"].unlink()

        status = _status_by_label(run_checks(**kwargs, standalone=False))

        self.assertEqual(status["pack.docs-present"], "FAIL")

    def test_missing_report_passes_in_a_standalone_distribution(self) -> None:
        """extract_public.py never ships it, so its absence there is the export's
        own policy — reporting that back as a defect is the verifier being wrong,
        not the pack being broken."""
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        kwargs["report_path"].unlink()

        results = run_checks(**kwargs, standalone=True)

        self.assertEqual(_status_by_label(results)["pack.docs-present"], "OK")
        self.assertEqual(summarize(results)[1], 0)

    def test_scorecard_artifact_check_exempts_the_internal_only_report_when_standalone(self) -> None:
        """The same document, named by the scorecard instead of check (d)."""
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        kwargs["report_path"].unlink()
        scorecard = json.loads(kwargs["scorecard_path"].read_text(encoding="utf-8"))
        scorecard["categories"][0]["requiredPackArtifacts"].append(INTERNAL_ONLY_ARTIFACTS[0])
        kwargs["scorecard_path"].write_text(json.dumps(scorecard), encoding="utf-8")

        nested = _status_by_label(run_checks(**kwargs, standalone=False))
        standalone = _status_by_label(run_checks(**kwargs, standalone=True))

        self.assertEqual(nested["pack.scorecard-artifacts"], "FAIL")
        self.assertEqual(standalone["pack.scorecard-artifacts"], "OK")

    def test_standalone_mode_still_fails_a_missing_report_it_does_ship(self) -> None:
        """The exemption is not 'reports/ does not matter here'. Only the declared
        internal-only documents are absent by design; any other required artifact
        missing from a standalone pack is still rot."""
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        scorecard = json.loads(kwargs["scorecard_path"].read_text(encoding="utf-8"))
        scorecard["categories"][0]["requiredPackArtifacts"].append("reports/shipped-report.md")
        kwargs["scorecard_path"].write_text(json.dumps(scorecard), encoding="utf-8")

        status = _status_by_label(run_checks(**kwargs, standalone=True))

        self.assertEqual(status["pack.scorecard-artifacts"], "FAIL")

    def test_missing_backlog_fails_in_both_modes(self) -> None:
        """The backlog ships in both layouts, so its absence is never by design."""
        for standalone in (False, True):
            with self.subTest(standalone=standalone):
                sandbox = self._sandbox()
                kwargs = self._build_pack(sandbox)
                kwargs["backlog_path"].unlink()

                status = _status_by_label(run_checks(**kwargs, standalone=standalone))

                self.assertEqual(status["pack.docs-present"], "FAIL")

    def test_stale_status_audit_warns_when_verifier_exists(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox, backlog_claims_none=True)

        status = _status_by_label(run_checks(**kwargs))

        self.assertEqual(status["pack.status-audit-stale"], "WARN")

    def test_absent_runtime_script_warns(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        (sandbox / "runtime" / "scripts" / "present.py").unlink()

        results = run_checks(**kwargs)
        status = _status_by_label(results)

        self.assertEqual(status["pack.runtime-scripts"], "WARN")
        self.assertEqual(summarize(results)[1], 0)

    def test_missing_runtime_root_warns_without_crash(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        kwargs["runtime_root_dir"] = sandbox / "no_such_runtime"

        status = _status_by_label(run_checks(**kwargs))

        self.assertEqual(status["pack.runtime-scripts"], "WARN")

    def test_invalid_policy_json_fails(self) -> None:
        sandbox = self._sandbox()
        kwargs = self._build_pack(sandbox)
        kwargs["policy_paths"][1].write_text("{not json", encoding="utf-8")

        status = _status_by_label(run_checks(**kwargs))

        self.assertEqual(status["pack.policies-load"], "FAIL")


if __name__ == "__main__":
    unittest.main()
