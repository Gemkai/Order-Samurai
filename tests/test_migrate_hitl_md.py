"""Tests for the one-shot needs_human_*.md -> hitl_queue.json migration
(bin/migrate_hitl_md.py).

migrate() already treats an OSError while parsing one ticket as a per-file
skip so the rest of the batch still completes. A malformed (non-numeric)
"Consecutive Failed Runs" field hit an uncaught int() ValueError instead,
aborting the whole migration mid-loop -- after any earlier tickets in the
sorted batch had already been migrated and their .md files deleted, leaving
a half-migrated state on every re-run.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bin.migrate_hitl_md import migrate  # type: ignore[import-not-found]


_GOOD_TICKET = """\
# Needs Human: some-skill

- **Remediation Command**: `/some-skill`
- **Metric ID**: `Some_Metric`
- **Pillar**: `bow`
- **Consecutive Failed Runs**: `3`

## Recommended Intervention
Investigate manually.
---
"""

_MALFORMED_TICKET = """\
# Needs Human: other-skill

- **Remediation Command**: `/other-skill`
- **Metric ID**: `Other_Metric`
- **Pillar**: `sword`
- **Consecutive Failed Runs**: `N/A`

## Recommended Intervention
Legacy hand-edited ticket with a non-numeric field.
---
"""

# _KV_RE captures backtick content and _parse_md strips it, so a
# Remediation Command field present but holding only whitespace between the
# backticks yields fields["Remediation Command"] == "" -- a value, not a
# missing key, so the "/unknown" .get() default never kicks in.
_BLANK_REMEDIATION_TICKET = """\
# Needs Human: blank-remediation

- **Remediation Command**: `   `
- **Metric ID**: `Some_Metric`
- **Pillar**: `bow`
- **Consecutive Failed Runs**: `1`

## Recommended Intervention
Ticket with a present-but-blank Remediation Command field.
---
"""


def _backlog_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state" / "backlog"
    d.mkdir(parents=True)
    return d


def test_malformed_consecutive_failed_runs_does_not_abort_the_batch(tmp_path):
    backlog = _backlog_dir(tmp_path)
    (backlog / "needs_human_aaa_good.md").write_text(_GOOD_TICKET, encoding="utf-8")
    (backlog / "needs_human_bbb_malformed.md").write_text(_MALFORMED_TICKET, encoding="utf-8")

    migrated, skipped = migrate(tmp_path, dry_run=True)

    # The malformed ticket must not raise and abort processing of the rest
    # of the sorted batch -- the good ticket still migrates, the malformed
    # one is skipped like any other per-file parse failure.
    assert migrated == 1
    assert skipped == 1


def test_malformed_consecutive_failed_runs_alone_is_handled(tmp_path):
    backlog = _backlog_dir(tmp_path)
    (backlog / "needs_human_only_malformed.md").write_text(_MALFORMED_TICKET, encoding="utf-8")

    # Must not raise.
    migrated, skipped = migrate(tmp_path, dry_run=True)
    assert migrated == 0
    assert skipped == 1


def test_blank_remediation_command_value_does_not_crash(tmp_path):
    backlog = _backlog_dir(tmp_path)
    (backlog / "needs_human_blank.md").write_text(_BLANK_REMEDIATION_TICKET, encoding="utf-8")

    # A present-but-whitespace-only Remediation Command must not raise
    # IndexError from "".split()[0] -- it should fall back like a missing field.
    migrated, skipped = migrate(tmp_path, dry_run=True)
    assert migrated == 1
    assert skipped == 0
