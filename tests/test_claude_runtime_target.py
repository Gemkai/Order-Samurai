from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution import claude_runtime_target as target  # type: ignore[attr-defined]


class ClaudeRuntimeTargetTests(unittest.TestCase):
    def test_all_policy_paths_exist(self) -> None:
        for path in target.ALL_POLICY_PATHS:
            with self.subTest(policy=path.name):
                self.assertTrue(path.exists(), f"missing pack policy: {path}")

    def test_report_and_backlog_exist(self) -> None:
        """In the repo both must exist; in a standalone distribution the
        hardening report is on the exporter's never-ship list, so requiring it
        there reported the export's own policy back as pack rot."""
        self.assertTrue(target.BACKLOG_PATH.exists())
        if not target.is_standalone_distribution():
            self.assertTrue(target.REPORT_PATH.exists())

    def test_runtime_root_defaults_to_home_claude(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_RUNTIME_ROOT", None)
            self.assertEqual(target.runtime_root(), Path.home() / ".claude")

    def test_runtime_root_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_RUNTIME_ROOT": "/tmp/sandbox-claude"}):
            self.assertEqual(target.runtime_root(), Path("/tmp/sandbox-claude"))


class AgenticaRepoRootTests(unittest.TestCase):
    """The marker resolver that replaced two fixed parent-hops.

    Its failure mode is asymmetric: a wrong root made verifiers measure an
    unrelated directory, but a spurious None makes root-hygiene a passing no-op.
    Both directions are pinned here, and the live repo is asserted to resolve so
    a marker regression cannot hide behind the standalone skip.
    """

    def _tree(self, *rel: str) -> Path:
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for r in rel:
            (root / r).mkdir(parents=True, exist_ok=True)
        return root.resolve()  # macOS /var -> /private/var

    def test_finds_the_root_above_a_nested_pack(self) -> None:
        root = self._tree("Governance/agentica_core", "Governance/Order Samurai/execution")
        here = root / "Governance" / "Order Samurai" / "execution" / "m.py"
        self.assertEqual(target.agentica_repo_root(here), root)

    def test_returns_none_for_a_flat_standalone_pack(self) -> None:
        """The public export: the pack IS the root, no Governance ancestor."""
        root = self._tree("agentica_core", "execution")
        self.assertIsNone(target.agentica_repo_root(root / "execution" / "m.py"))

    def test_a_governance_dir_without_agentica_core_is_not_the_marker(self) -> None:
        root = self._tree("Governance/Order Samurai/execution")
        self.assertIsNone(
            target.agentica_repo_root(root / "Governance" / "Order Samurai" / "execution" / "m.py"))

    def test_this_repo_resolves(self) -> None:
        """Canary: if this ever returns None in-repo, root hygiene silently
        becomes a no-op instead of failing loudly."""
        if target.is_standalone_distribution():
            self.skipTest("standalone distribution — no repo root by design")
        root = target.agentica_repo_root()
        assert root is not None  # narrows for the type checker; asserted below too
        self.assertTrue((root / "Governance" / "agentica_core").is_dir())


class PinnedHomePathsTests(unittest.TestCase):
    """The shared denylist matcher for every verify_claude_* verifier.

    It replaced per-verifier tuples of literal paths, one of which was this
    machine's own home. The public exporter rewrites "/Users/<owner>/.claude"
    to "~/.claude", so that literal became the PORTABLE form the verifiers exist
    to accept — inverting every check in the exported tree, where nobody looked.
    Hence the two groups below: encodings that must match, and portable spellings
    that must never match. The second group is the one that was broken.
    """

    def test_matches_every_absolute_encoding(self) -> None:
        for text, label in (
            ("/Users/someone/.claude", "posix"),
            ("/home/someone/.claude", "linux"),
            (r"C:\Users\someone\.claude", "windows backslash"),
            (r"C:\\Users\\someone\\.claude", "json-doubled backslash"),
            ("C:/Users/someone/.claude", "windows forward slash"),
            ("/Volumes/Users/someone/.claude", "home reached through a mount"),
        ):
            with self.subTest(encoding=label):
                self.assertTrue(target.pinned_home_paths(text, ".claude"), text)

    def test_never_matches_a_portable_spelling(self) -> None:
        """The regression that shipped: each of these is the CORRECT form."""
        for text, label in (
            ("~/.claude/scripts/x.py", "tilde"),
            ("$HOME/.claude", "env var"),
            ("${HOME}/.claude", "braced env var"),
            ('Path.home() / ".claude"', "python"),
            ('os.path.expanduser("~/.claude/data")', "expanduser"),
        ):
            with self.subTest(form=label):
                self.assertEqual(target.pinned_home_paths(text, ".claude"), [], text)

    def test_reports_one_canonical_spelling_per_path(self) -> None:
        """A doubled-backslash hit and its single-backslash twin are one finding,
        not two — offender strings are asserted in tests and read by humans."""
        both = r'{"a": "C:\\Users\\someone\\.claude", "b": "C:\Users\someone\.claude"}'
        self.assertEqual(target.pinned_home_paths(both, ".claude"),
                         [r"C:\Users\someone\.claude"])

    def test_matches_a_nested_runtime_dir(self) -> None:
        self.assertTrue(
            target.pinned_home_paths("/Users/someone/.gemini/antigravity/config.json",
                                     ".gemini/antigravity"))

    def test_carries_no_machine_identifier_of_its_own(self) -> None:
        """The property that keeps this fix from regressing: if a future edit
        reintroduces a literal home here, the exporter will scrub it and the
        inversion returns silently."""
        source = Path(target.__file__).read_text(encoding="utf-8")
        for token in ("/Users/", "C:\\Users\\", "C:/Users/"):
            with self.subTest(token=token):
                self.assertNotIn(token + "someone", source)
        self.assertNotIn(str(Path.home()), source)


if __name__ == "__main__":
    unittest.main()
