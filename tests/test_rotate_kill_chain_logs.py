from __future__ import annotations

import json
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from bin.rotate_kill_chain_logs import rotate_file  # type: ignore[attr-defined]


NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
HEADER = '# Schema: {"ts": "ISO-8601", "confidence": 0.0..1.0}\n'


def row(ts: datetime, i: int = 0) -> str:
    return json.dumps({"ts": ts.isoformat().replace("+00:00", "Z"),
                       "event_type": "test", "confidence": 0.5, "n": i}) + "\n"


class RotateKillChainLogsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = REPO_ROOT / ".tmp" / "test_rotate_kill_chain_logs" / self._testMethodName
        # Rotation archives with append mode; a stale sandbox from a prior run
        # would double the archived count. Start clean so tests are re-runnable.
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.archive = self.sandbox / "logs" / "rotated"

    def _write(self, name: str, lines: list[str]) -> Path:
        path = self.sandbox / name
        path.write_text("".join(lines), encoding="utf-8")
        return path

    def _rotate(self, path: Path, *, max_lines: int = 10, keep_lines: int = 5,
                max_age_days: int = 90, now: datetime = NOW, dry_run: bool = False) -> str:
        return rotate_file(path, self.archive, max_lines=max_lines, keep_lines=keep_lines,
                           max_age_days=max_age_days, now=now, dry_run=dry_run)

    def test_within_bounds_untouched(self) -> None:
        lines = [HEADER] + [row(NOW - timedelta(days=1), i) for i in range(3)]
        path = self._write("kill_chain_unmatched.jsonl", lines)
        summary = self._rotate(path)
        self.assertIn("no rotation", summary)
        self.assertEqual(path.read_text(encoding="utf-8"), "".join(lines))
        self.assertFalse(self.archive.exists())

    def test_line_cap_keeps_newest_and_preserves_header(self) -> None:
        rows = [row(NOW - timedelta(hours=20 - i), i) for i in range(20)]
        path = self._write("kill_chain_unmatched.jsonl", [HEADER] + rows)
        summary = self._rotate(path)
        self.assertIn("archived 15", summary)
        kept = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(kept[0], HEADER.strip())
        self.assertEqual(len(kept), 1 + 5)
        self.assertEqual(json.loads(kept[-1])["n"], 19)  # newest survives
        archived = (self.archive / "kill_chain_unmatched-rotated-2026-07-13.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(archived), 15)
        self.assertEqual(json.loads(archived[0])["n"], 0)  # oldest archived

    def test_age_rotation_archives_old_rows(self) -> None:
        old = [row(NOW - timedelta(days=200), i) for i in range(3)]
        fresh = [row(NOW - timedelta(days=1), i + 3) for i in range(2)]
        path = self._write("kill_chain_events.jsonl", old + fresh)
        summary = self._rotate(path)
        self.assertIn("archived 3", summary)
        kept = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(kept), 2)

    def test_unparseable_rows_follow_neighbors(self) -> None:
        lines = ([row(NOW - timedelta(days=200), 0), "not json\n"]
                 + [row(NOW - timedelta(days=1), 1)])
        path = self._write("kill_chain_events.jsonl", lines)
        self._rotate(path)
        kept = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(kept, [lines[2].strip()])

    def test_all_unparseable_never_age_rotates(self) -> None:
        lines = ["junk one\n", "junk two\n"]
        path = self._write("kill_chain_events.jsonl", lines)
        summary = self._rotate(path)
        self.assertIn("no rotation", summary)

    def test_dry_run_modifies_nothing(self) -> None:
        rows = [row(NOW - timedelta(days=200), i) for i in range(20)]
        path = self._write("kill_chain_unmatched.jsonl", [HEADER] + rows)
        summary = self._rotate(path, dry_run=True)
        self.assertIn("DRY RUN", summary)
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 21)
        self.assertFalse(self.archive.exists())

    def test_absent_file_skipped(self) -> None:
        summary = self._rotate(self.sandbox / "missing.jsonl")
        self.assertIn("absent", summary)


if __name__ == "__main__":
    unittest.main()
