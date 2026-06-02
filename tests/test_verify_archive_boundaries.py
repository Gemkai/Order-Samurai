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
