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


    def test_mixed_record_excludes_hiring_review_from_adherence(self) -> None:
        ts = self._ts()
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": ts,
            "categories": ["hiring-review", "review"],
            "skills": ["/hire", "/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [
            {"session_id": "s1", "ts": ts, "skill": "/hire"},
            {"session_id": "s1", "ts": ts, "skill": "/security-audit"},
        ])
        result = sra.compute_adherence()
        self.assertEqual((result["sample_size"], result["routed"]), (1, 1))
        self.assertEqual(result["val"], 100.0)

    def test_mixed_record_excludes_hiring_review_from_work_volume(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["hiring-review", "review"],
            "skills": ["/hire", "/security-audit"],
        }])
        self.assertEqual(sra.compute_work_volume()["val"], 1)

    def test_hiring_review_exclusion_requires_exact_category(self) -> None:
        ts = self._ts()
        for category in ("Hiring-review", "hiring-review-extra"):
            with self.subTest(category=category):
                self._write_jsonl(sra.DETECT, [{
                    "session_id": "s1", "ts": ts,
                    "categories": [category], "skills": ["/hire"],
                }])
                self._write_jsonl(sra.INVOKE, [
                    {"session_id": "s1", "ts": ts, "skill": "/hire"},
                ])
                result = sra.compute_adherence()
                self.assertEqual((result["sample_size"], result["routed"]), (1, 1))
                self.assertEqual(sra.compute_work_volume()["val"], 1)

    def test_invocation_before_detection_does_not_credit(self) -> None:
        from datetime import datetime, timedelta, timezone
        detection = datetime.now(timezone.utc) - timedelta(hours=1)
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": detection.isoformat(),
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": (detection - timedelta(minutes=1)).isoformat(),
            "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 0)
        self.assertEqual(result["val"], 0.0)

    def test_invocation_at_detection_timestamp_credits(self) -> None:
        ts = self._ts()
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": ts,
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": ts, "skill": "/security-audit",
        }])
        self.assertEqual(sra.compute_adherence()["routed"], 1)

    def test_later_invocation_matches_normalized_leading_skill_slug(self) -> None:
        from datetime import datetime, timedelta, timezone
        detection = datetime.now(timezone.utc) - timedelta(hours=1)
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": detection.isoformat(),
            "categories": ["review"], "skills": ["/security-audit target"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": (detection + timedelta(minutes=1)).isoformat(),
            "skill": "security-audit other-target",
        }])
        self.assertEqual(sra.compute_adherence()["routed"], 1)

    def test_invocation_in_other_session_does_not_credit(self) -> None:
        ts = self._ts()
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": ts,
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s2", "ts": ts, "skill": "/security-audit",
        }])
        self.assertEqual(sra.compute_adherence()["routed"], 0)

    def test_invocation_of_other_skill_does_not_credit(self) -> None:
        ts = self._ts()
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": ts,
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": ts, "skill": "/other-skill",
        }])
        self.assertEqual(sra.compute_adherence()["routed"], 0)

    def test_later_invocation_across_timestamp_formats_credits(self) -> None:
        from datetime import datetime, timedelta, timezone
        offset = timezone(timedelta(hours=-4))
        detection = (datetime.now(offset) - timedelta(days=2)).replace(
            hour=23, minute=0, second=0, microsecond=0,
        )
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": detection.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1",
            "ts": (detection + timedelta(minutes=30)).astimezone(timezone.utc).isoformat(),
            "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 1)

    def test_earlier_invocation_across_timestamp_formats_does_not_credit(self) -> None:
        from datetime import datetime, timedelta, timezone
        offset = timezone(timedelta(hours=-4))
        detection = (datetime.now(offset) - timedelta(days=2)).replace(
            hour=23, minute=0, second=0, microsecond=0,
        )
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": detection.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        # UTC's next calendar day still precedes this detection by 30 minutes.
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1",
            "ts": (detection - timedelta(minutes=30)).astimezone(timezone.utc).isoformat(),
            "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 0)

    def test_invocation_missing_timestamp_does_not_credit(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 0)

    def test_invocation_unparseable_timestamp_does_not_credit(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["review"], "skills": ["/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": "not-a-timestamp", "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 0)

    def test_only_hiring_review_returns_no_adherence_data(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["hiring-review"], "skills": ["/hire"],
        }])
        self._write_jsonl(sra.INVOKE, [])
        result = sra.compute_adherence()
        self.assertIsNone(result["val"])
        self.assertIs(result["is_simulated"], True)
        self.assertEqual((result["sample_size"], result["routed"]), (0, 0))

    def test_only_hiring_review_returns_no_work_volume_data(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["hiring-review"], "skills": ["/hire"],
        }])
        self.assertIsNone(sra.compute_work_volume()["val"])

    def test_only_hiring_review_work_volume_is_simulated(self) -> None:
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": self._ts(),
            "categories": ["hiring-review"], "skills": ["/hire"],
        }])
        self.assertIs(sra.compute_work_volume()["is_simulated"], True)

    def test_hiring_review_exclusion_preserves_same_skill_in_other_category(self) -> None:
        from datetime import datetime, timedelta, timezone
        detection = datetime.now(timezone.utc) - timedelta(hours=1)
        self._write_jsonl(sra.DETECT, [{
            "session_id": "s1", "ts": detection.isoformat(),
            "categories": ["hiring-review", "review"],
            "skills": ["/security-audit", "/security-audit"],
        }])
        self._write_jsonl(sra.INVOKE, [{
            "session_id": "s1", "ts": (detection + timedelta(minutes=1)).isoformat(),
            "skill": "/security-audit",
        }])
        result = sra.compute_adherence()
        self.assertEqual(result["sample_size"], 1)
        self.assertEqual(result["routed"], 1)

    def test_parse_ts_accepts_colonless_offset_as_aware_datetime(self) -> None:
        from datetime import datetime, timedelta, timezone
        offset = timezone(timedelta(hours=-4))
        timestamp = (datetime.now(offset) - timedelta(hours=1)).replace(microsecond=0)
        parsed = sra._parse_ts(timestamp.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.assertIsInstance(parsed, datetime)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertIsNotNone(parsed.utcoffset())
        self.assertEqual(parsed, timestamp)


if __name__ == "__main__":
    unittest.main()
