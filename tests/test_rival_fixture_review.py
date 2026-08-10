"""rival_fixture_review: the weekly rival self-audit's list/record bookkeeping.

Uses the REAL fixture directory (tests/rival_fixtures/) since these tests only
exercise deterministic Python (spacing guard, discovery, grading) — never a real
rival call, which only sensei-orchestrator.ts's AgentRunner can make.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import rival_fixture_review as review  # type: ignore[import-not-found]
from agentica_core import rival_audit_lineage  # type: ignore[import-not-found]

REAL_FIXTURES_ROOT = REPO_ROOT / "tests" / "rival_fixtures"


def _paths(tmp_path):
    return {
        "lineage_path": tmp_path / "rival_audit_lineage.jsonl",
        "self_audit_path": tmp_path / "rival_self_audit.jsonl",
    }


# ---------------------------------------------------------------------------
# list_pending — spacing guard
# ---------------------------------------------------------------------------

def test_first_round_ever_is_not_spaced_out(tmp_path):
    paths = _paths(tmp_path)
    out = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    assert out["ran"] is True
    assert len(out["fixtures"]) == 6  # all 6 real fixtures, none yet covered this round


def test_spacing_guard_skips_a_recent_round(tmp_path):
    paths = _paths(tmp_path)
    rival_audit_lineage.append_entry(
        {"round": 1, "decision": "ran", "reason": "1 fixture(s) due"}, paths["lineage_path"])
    out = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    assert out["ran"] is False
    assert "spacing" in out["reason"]
    assert out["fixtures"] == []


def test_spacing_guard_skip_writes_nothing_to_the_lineage(tmp_path):
    paths = _paths(tmp_path)
    rival_audit_lineage.append_entry(
        {"round": 1, "decision": "ran", "reason": "x"}, paths["lineage_path"])
    before = list(rival_audit_lineage.iter_entries(paths["lineage_path"]))
    review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    after = list(rival_audit_lineage.iter_entries(paths["lineage_path"]))
    assert after == before


# ---------------------------------------------------------------------------
# list_pending — dry run writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_reports_fixtures_but_writes_no_lineage_row(tmp_path):
    paths = _paths(tmp_path)
    out = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, dry_run=True, **paths)
    assert out["ran"] is True
    assert len(out["fixtures"]) == 6
    assert list(rival_audit_lineage.iter_entries(paths["lineage_path"])) == []


def test_dry_run_does_not_consume_the_round_for_a_later_real_call(tmp_path):
    paths = _paths(tmp_path)
    review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, dry_run=True, **paths)
    out = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, dry_run=False, **paths)
    assert out["round"] == 1
    assert len(out["fixtures"]) == 6


# ---------------------------------------------------------------------------
# list_pending — round-level bookkeeping, not per-fixture
# ---------------------------------------------------------------------------

def test_list_writes_exactly_one_lineage_row_for_all_fixtures_in_the_round(tmp_path):
    paths = _paths(tmp_path)
    review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    rows = list(rival_audit_lineage.iter_entries(paths["lineage_path"]))
    assert len(rows) == 1  # not six
    assert rows[0]["decision"] == "ran"
    assert rows[0]["round"] == 1


def test_a_fixture_already_covered_this_round_is_excluded_on_a_second_list_call(tmp_path):
    paths = _paths(tmp_path)
    out1 = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    round_no = out1["round"]
    fid = out1["fixtures"][0]["fixture_id"]
    review.record(fid, round_no, "REFUTED", confidence="high", reasoning="r", evidence="e",
                  fixtures_root=REAL_FIXTURES_ROOT, self_audit_path=paths["self_audit_path"])

    # Same round (spacing guard would normally block a second --list this soon in
    # production, but the caller is allowed to re-list mid-round after a partial
    # crash/resume — verify discovery correctly narrows to what's left).
    remaining_ids = {f["fixture_id"] for f in out1["fixtures"]} - {fid}
    # Directly exercise the exclusion helper rather than re-triggering the spacing
    # guard (a second list_pending call this soon would legitimately be spaced out).
    assert review._rounds_already_covered(fid, round_no, paths["self_audit_path"]) is True
    for other in remaining_ids:
        assert review._rounds_already_covered(other, round_no, paths["self_audit_path"]) is False


def test_a_fixture_is_reusable_in_a_later_round(tmp_path):
    paths = _paths(tmp_path)
    out1 = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    fid = out1["fixtures"][0]["fixture_id"]
    review.record(fid, out1["round"], "REFUTED", confidence="high", reasoning="r", evidence="e",
                  fixtures_root=REAL_FIXTURES_ROOT, self_audit_path=paths["self_audit_path"])
    # A later round (round 2) has NOT covered this fixture yet, even though round 1 did.
    assert review._rounds_already_covered(fid, out1["round"] + 1, paths["self_audit_path"]) is False


# ---------------------------------------------------------------------------
# list_pending — no candidates
# ---------------------------------------------------------------------------

def test_no_fixtures_found_writes_skipped_no_candidate(tmp_path):
    paths = _paths(tmp_path)
    empty_root = tmp_path / "empty_fixtures"
    empty_root.mkdir()
    out = review.list_pending(fixtures_root=empty_root, **paths)
    assert out["ran"] is True
    assert out["fixtures"] == []
    rows = list(rival_audit_lineage.iter_entries(paths["lineage_path"]))
    assert rows[0]["decision"] == "skipped_no_candidate"


# ---------------------------------------------------------------------------
# list_pending — fixture content shape (absolute source_file resolution)
# ---------------------------------------------------------------------------

def test_relative_source_file_is_resolved_to_an_absolute_path(tmp_path):
    paths = _paths(tmp_path)
    out = review.list_pending(fixtures_root=REAL_FIXTURES_ROOT, **paths)
    for f in out["fixtures"]:
        sf = f["scout_finding"]["evidence"]["source_file"]
        assert sf.startswith("/"), f"{f['fixture_id']}: source_file not absolute: {sf}"
        assert Path(sf).is_file(), f"{f['fixture_id']}: resolved source_file does not exist: {sf}"


# ---------------------------------------------------------------------------
# record — grading
# ---------------------------------------------------------------------------

def test_record_scores_a_matching_verdict_as_passed(tmp_path):
    paths = _paths(tmp_path)
    row = review.record(
        "confirmed-scheduled-job-failures", 1, "CONFIRMED",
        confidence="high", reasoning="matches", evidence="checked evidence.jsonl",
        fixtures_root=REAL_FIXTURES_ROOT, self_audit_path=paths["self_audit_path"],
    )
    assert row["passed"] is True
    assert row["seeded_verdict"] == "CONFIRMED"
    assert row["actual_verdict"] == "CONFIRMED"


def test_record_scores_a_mismatched_verdict_as_failed(tmp_path):
    paths = _paths(tmp_path)
    row = review.record(
        "tautological-simplify-age", 1, "CONFIRMED",  # seeded expects REFUTED
        confidence="high", reasoning="missed it", evidence="n/a",
        fixtures_root=REAL_FIXTURES_ROOT, self_audit_path=paths["self_audit_path"],
    )
    assert row["passed"] is False
    assert row["seeded_verdict"] == "REFUTED"
    assert row["actual_verdict"] == "CONFIRMED"


def test_record_appends_not_overwrites(tmp_path):
    paths = _paths(tmp_path)
    review.record("confirmed-scheduled-job-failures", 1, "CONFIRMED", confidence="high",
                  reasoning="a", evidence="a", fixtures_root=REAL_FIXTURES_ROOT,
                  self_audit_path=paths["self_audit_path"])
    review.record("confirmed-context-cliff", 1, "CONFIRMED", confidence="high",
                  reasoning="b", evidence="b", fixtures_root=REAL_FIXTURES_ROOT,
                  self_audit_path=paths["self_audit_path"])
    lines = paths["self_audit_path"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_record_raises_on_unknown_fixture_id(tmp_path):
    paths = _paths(tmp_path)
    try:
        review.record("does-not-exist", 1, "CONFIRMED", confidence="high",
                      reasoning="x", evidence="x", fixtures_root=REAL_FIXTURES_ROOT,
                      self_audit_path=paths["self_audit_path"])
        assert False, "expected KeyError"
    except KeyError:
        pass
    assert not paths["self_audit_path"].exists()


def test_record_rejects_unknown_verdict_vocabulary(tmp_path):
    paths = _paths(tmp_path)
    try:
        review.record("confirmed-scheduled-job-failures", 1, "PROBABLY",
                      confidence="high", reasoning="x", evidence="x",
                      fixtures_root=REAL_FIXTURES_ROOT, self_audit_path=paths["self_audit_path"])
        assert False, "expected ValueError"
    except ValueError:
        pass
