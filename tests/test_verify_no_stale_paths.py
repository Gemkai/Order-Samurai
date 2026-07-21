from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from execution.verify_no_stale_paths import (  # type: ignore[attr-defined]
    STALE_LITERALS,
    scan_stale_literals,
    summarize,
)


class VerifyNoStalePathsTests(unittest.TestCase):
    def _sandbox(self) -> Path:
        sandbox = REPO_ROOT / ".tmp" / "test_verify_no_stale_paths" / self._testMethodName
        sandbox.mkdir(parents=True, exist_ok=True)
        return sandbox

    def test_flags_json_escaped_desktop_literal(self) -> None:
        # JSON escapes backslashes, so the real config stored C:\\Users\\example\\Desktop —
        # the scan must catch the doubled form, not just the single-backslash literal.
        sandbox = self._sandbox()
        (sandbox / "surface.json").write_text(
            '{"targetRoot": "C:\\\\Users\\\\example\\\\Desktop\\\\Agentica OS"}\n',
            encoding="utf-8",
        )

        offenders = scan_stale_literals(
            scan_paths=[sandbox], literals=STALE_LITERALS, base_root=sandbox
        )

        self.assertEqual(offenders, [r"surface.json (C:\Users\example\Desktop)"])

    def test_flags_retired_lm_studio_endpoint(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "routing.md").write_text(
            "Local LLM: http://localhost:1234/v1\n", encoding="utf-8"
        )

        offenders = scan_stale_literals(
            scan_paths=[sandbox], literals=STALE_LITERALS, base_root=sandbox
        )

        self.assertEqual(offenders, ["routing.md (localhost:1234)"])

    def test_ignores_current_root(self) -> None:
        sandbox = self._sandbox()
        (sandbox / "surface.json").write_text(
            '{"targetRoot": "C:\\\\Users\\\\example\\\\Agentica-OS"}\n', encoding="utf-8"
        )

        offenders = scan_stale_literals(
            scan_paths=[sandbox], literals=STALE_LITERALS, base_root=sandbox
        )

        self.assertEqual(offenders, [])

    def test_summarize_sets_nonzero_exit_for_failures(self) -> None:
        counts, exit_code = summarize(
            [
                {"status": "OK", "label": "a", "detail": "x"},
                {"status": "FAIL", "label": "b", "detail": "y"},
            ]
        )

        self.assertEqual(counts["FAIL"], 1)
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
