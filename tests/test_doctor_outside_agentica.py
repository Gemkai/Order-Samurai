"""doctor must distinguish "this check does not apply here" from "this install is broken".

The public export ships doctor but not the Agentica tree around it. Three families walk
UP out of the export — `../api`, `../.planning`, `../Execution` — so outside an Agentica
checkout they resolved into somebody's home directory and reported
"unreadable: No such file or directory: /Users/.../.planning/GOAL_REGISTRY.jsonl". A
fourth FAILed on PROJECT.md and RONIN_SPEC.md, which the export deliberately withdrew on
2026-08-16: the gate reporting the product's own policy back as a defect.

Audit 2026-09-01, finding B3. The rule these tests pin: outside an Agentica tree the
STATUS may soften, but the row must still name what is missing and why it does not apply.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution import doctor  # noqa: E402
from execution import verify_runtime_contract as vrc  # noqa: E402


class _Standalone:
    """Patch the distribution detector for the duration of a test."""

    def __init__(self, value: bool) -> None:
        self.value = value

    def __enter__(self):
        from execution import claude_runtime_target as crt
        self._real = crt.is_standalone_distribution
        crt.is_standalone_distribution = lambda: self.value
        return self

    def __exit__(self, *_exc):
        from execution import claude_runtime_target as crt
        crt.is_standalone_distribution = self._real
        return False


class FactoryFamilyTest(unittest.TestCase):
    def test_outside_agentica_it_says_not_applicable_instead_of_missing_files(self):
        with _Standalone(True):
            rows = doctor._run_factory_checks()
        self.assertEqual([r["status"] for r in rows], ["WARN"])
        self.assertEqual(rows[0]["label"], "factory.not-applicable-outside-agentica")
        detail = rows[0]["detail"]
        # It must still name WHAT it skipped and WHY — a bare "skipped" is the same
        # silence in a politer voice.
        for token in ("queue", "plist-drift", "ledger-chain", ".planning/",
                      "Execution/factory/", "standalone distribution"):
            self.assertIn(token, detail)
        self.assertNotIn("No such file", detail)

    def test_inside_agentica_the_families_still_run(self):
        """The softening must not disarm the check where it is the real signal."""
        with _Standalone(False):
            rows = doctor._run_factory_checks()
        labels = {r["label"] for r in rows}
        self.assertNotIn("factory.not-applicable-outside-agentica", labels)
        self.assertTrue(any(label.startswith("factory.queue") for label in labels))


class ExecChainFamilyTest(unittest.TestCase):
    def test_outside_agentica_it_names_the_missing_package_not_a_path(self, ):
        with _Standalone(True):
            rows = doctor._run_exec_chain_checks(api_dir=Path("/nonexistent/api"))
        self.assertEqual(rows[0]["label"], "exec-chain.not-applicable-outside-agentica")
        self.assertIn("api/", rows[0]["detail"])
        self.assertNotIn("/nonexistent", rows[0]["detail"])

    def test_inside_agentica_a_missing_verifier_still_names_the_path(self):
        with _Standalone(False):
            rows = doctor._run_exec_chain_checks(api_dir=Path("/nonexistent/api"))
        self.assertEqual(rows[0]["label"], "exec-chain")
        self.assertIn("/nonexistent", rows[0]["detail"])


class WithdrawnArtifactsTest(unittest.TestCase):
    def test_an_artifact_the_export_withdrew_is_not_a_failure(self):
        manifest = "# generated\nPROJECT.md\nRONIN_SPEC.md\n"
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".export-withdrawn").write_text(manifest, encoding="utf-8")
            withdrawn = vrc._withdrawn_from_this_export(root)
        self.assertEqual(withdrawn, {"PROJECT.md", "RONIN_SPEC.md"})

    def test_comments_and_blank_lines_are_not_treated_as_paths(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".export-withdrawn").write_text(
                "# a comment\n\n  # indented comment\nreal/path.md\n", encoding="utf-8")
            self.assertEqual(vrc._withdrawn_from_this_export(root), {"real/path.md"})

    def test_no_manifest_withdraws_nothing(self):
        """The in-repo case. An absent manifest must never excuse a real absence."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(vrc._withdrawn_from_this_export(Path(tmp)), set())


if __name__ == "__main__":
    unittest.main()
