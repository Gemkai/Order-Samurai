from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_archive_boundaries import (  # type: ignore[attr-defined]
    scan_archive_boundary_violations,
    summarize,
)


class VerifyArchiveBoundariesTests(unittest.TestCase):
    def test_scan_archive_boundary_violations_flags_scratch_reference(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_archive_boundaries" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        bad_file = live_root / "audit.py"
        bad_file.write_text('TARGET = "scratch/demo-app"\n', encoding="utf-8")

        offenders = scan_archive_boundary_violations(
            scan_paths=[live_root],
            forbidden_roots=("archive", "playground", "scratch"),
            base_root=sandbox,
        )

        self.assertEqual(offenders, ["execution/audit.py -> scratch"])

    def test_scan_archive_boundary_violations_ignores_clean_file(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_archive_boundaries" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        good_file = live_root / "clean.py"
        good_file.write_text('TARGET = "execution/runtime_paths.py"\n', encoding="utf-8")

        offenders = scan_archive_boundary_violations(
            scan_paths=[live_root],
            forbidden_roots=("archive", "playground", "scratch"),
            base_root=sandbox,
        )

        self.assertEqual(offenders, [])

    def test_summarize_sets_zero_exit_for_clean_results(self) -> None:
        counts, exit_code = summarize(
            [
                {"status": "OK", "label": "a", "detail": "x"},
                {"status": "WARN", "label": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["OK"], 1)
        self.assertEqual(counts["WARN"], 1)
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()


class VerifyArchiveBoundariesSymlinkTests(unittest.TestCase):
    def test_symlink_escaping_base_root_does_not_crash_scan(self) -> None:
        """A symlink inside a scan path resolving OUTSIDE base_root made
        resolve().relative_to(base_root) raise an uncaught ValueError and crash
        the whole verifier. It must scan without crashing and still report the
        offender by its in-tree path."""
        sandbox = REPO_ROOT / ".tmp" / "test_verify_archive_boundaries" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        outside = REPO_ROOT / ".tmp" / "test_verify_archive_boundaries" / (self._testMethodName + "_outside")
        outside.mkdir(parents=True, exist_ok=True)
        target = outside / "escaped.py"
        target.write_text('TARGET = "scratch/demo-app"\n', encoding="utf-8")
        link = live_root / "linked.py"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)

        offenders = scan_archive_boundary_violations(
            scan_paths=[live_root],
            forbidden_roots=("archive", "playground", "scratch"),
            base_root=sandbox,
        )

        self.assertEqual(len(offenders), 1)
        self.assertIn("linked.py", offenders[0])
        self.assertTrue(offenders[0].endswith("-> scratch"))
