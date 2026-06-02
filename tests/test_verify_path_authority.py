from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_path_authority import (  # type: ignore[attr-defined]
    scan_hardcoded_path_literals,
    summarize,
)


class VerifyPathAuthorityTests(unittest.TestCase):
    def test_scan_hardcoded_path_literals_flags_forward_slash_literal(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_path_authority" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        bad_file = live_root / "bad.py"
        bad_file.write_text(
            'ROOT = "C:/Users/jemak/Desktop/Projects/Order Samurai"\n',
            encoding="utf-8",
        )

        offenders = scan_hardcoded_path_literals(
            scan_paths=[live_root],
            path_literals=(
                "C:/Users/jemak/Desktop/Projects/Order Samurai",
                r"C:\Users\jemak\Desktop\Projects\Order Samurai",
            ),
            base_root=sandbox,
        )

        self.assertEqual(offenders, ["execution/bad.py"])

    def test_scan_hardcoded_path_literals_flags_backslash_literal(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_path_authority" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        bad_file = live_root / "bad.py"
        bad_file.write_text(
            'ROOT = "C:\\\\Users\\\\jemak\\\\Desktop\\\\Projects\\\\Order Samurai"\n',
            encoding="utf-8",
        )

        offenders = scan_hardcoded_path_literals(
            scan_paths=[live_root],
            path_literals=(
                "C:/Users/jemak/Desktop/Projects/Order Samurai",
                r"C:\Users\jemak\Desktop\Projects\Order Samurai",
            ),
            base_root=sandbox,
        )

        self.assertEqual(offenders, ["execution/bad.py"])

    def test_scan_hardcoded_path_literals_ignores_clean_file(self) -> None:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_path_authority" / self._testMethodName
        live_root = sandbox / "execution"
        live_root.mkdir(parents=True, exist_ok=True)
        good_file = live_root / "good.py"
        good_file.write_text(
            'from execution.runtime_paths import REPO_ROOT\n',
            encoding="utf-8",
        )

        offenders = scan_hardcoded_path_literals(
            scan_paths=[live_root],
            path_literals=(
                "C:/Users/jemak/Desktop/Projects/Order Samurai",
                r"C:\Users\jemak\Desktop\Projects\Order Samurai",
            ),
            base_root=sandbox,
        )

        self.assertEqual(offenders, [])

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = summarize(
            [
                {"status": "OK", "label": "a", "detail": "x"},
                {"status": "FAIL", "label": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["OK"], 1)
        self.assertEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
