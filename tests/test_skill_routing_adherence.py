"""Tests for execution/skill_routing_adherence.py — Skill_Routing_Adherence /
Governance_Work_Volume reducers (sword pillar).

Focus: an empty or missing `skill` field in either source log must not crash
compute_adherence() — the same shape of record it already tolerates via
`.get(..., "")` elsewhere, just missing the guard on the final `[0]` index.
"""
from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution import skill_routing_adherence as sra  # type: ignore[import-not-found]


class ComputeAdherenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = REPO_ROOT / ".tmp" / "test_skill_routing_adherence" / self._testMethodName
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self._orig_detect = sra.DETECT
        self._orig_invoke = sra.INVOKE
        sra.DETECT = self.sandbox / "skill_routing.jsonl"
        sra.INVOKE = self.sandbox / "skill_invocations.jsonl"

    def tearDown(self) -> None:
        sra.DETECT = self._orig_detect
        sra.INVOKE = self._orig_invoke

    def _write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    @staticmethod
    def _ts() -> str:
        # Every live hook record carries ts (530/530 verified 2026-08-25); since the
        # 30d windowing an undated detection is dropped, so fixtures stamp one.
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def test_empty_skill_field_in_invocation_does_not_crash(self) -> None:
        # A partially-written hook event can log skill="" — must not IndexError.
        self._write_jsonl(sra.INVOKE, [{"session_id": "s1", "skill": ""}])
        self._write_jsonl(sra.DETECT, [{"session_id": "s1", "ts": self._ts(),
                                        "categories": ["review"],
                                        "skills": ["/security-audit"]}])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 0)  # blank invocation never satisfies the detection

    def test_empty_skill_field_in_detection_does_not_crash(self) -> None:
        self._write_jsonl(sra.DETECT, [{"session_id": "s1", "ts": self._ts(),
                                        "categories": ["review"], "skills": [""]}])
        self._write_jsonl(sra.INVOKE, [])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 0)
        self.assertIsNone(result["val"])

    def test_out_of_window_detection_is_excluded(self) -> None:
        # WINDOWED 2026-08-25: a lifetime-old detection must no longer poison the
        # ratio (22 weeks flat at 3.9 with warn=80 unreachable was the defect).
        self._write_jsonl(sra.DETECT, [
            {"session_id": "old", "ts": "2026-01-01T00:00:00+00:00",
             "categories": ["review"], "skills": ["/security-audit"]},
            {"session_id": "s1", "ts": self._ts(),
             "categories": ["review"], "skills": ["/security-audit"]},
        ])
        self._write_jsonl(sra.INVOKE, [{"session_id": "s1", "skill": "/security-audit",
                                        "ts": self._ts()}])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)  # the old detection is out of window
        self.assertEqual(result["routed"], 1)
        self.assertEqual(result["val"], 100.0)

    def test_undated_detection_is_excluded(self) -> None:
        # No live record lacks ts; an undated one cannot be windowed, so it is
        # dropped rather than counted forever.
        self._write_jsonl(sra.DETECT, [{"session_id": "s1", "categories": ["review"],
                                        "skills": ["/security-audit"]}])
        self._write_jsonl(sra.INVOKE, [])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 0)
        self.assertIsNone(result["val"])


if __name__ == "__main__":
    unittest.main()
