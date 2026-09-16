"""execution/verifier_results.py — the shared verifier plumbing (M7.2).

26 verifiers each carried their own `_make_result` and `summarize`. Measured
before extracting: all 26 summarize bodies were behaviourally identical, and 25
of 26 make_result bodies were too. These pin the shared behaviour, including the
one real variation (the `name` label key) and the one judgement call (an unknown
status is counted, not dropped).
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution.verifier_results import STATUSES, make_result, render, summarize  # noqa: E402


# ── make_result ───────────────────────────────────────────────────────────────

def test_result_carries_status_label_and_detail():
    assert make_result("OK", "a.check", "all good") == {
        "status": "OK", "label": "a.check", "detail": "all good",
    }


def test_the_alternate_label_key_is_honoured():
    """verify_claude_root_hygiene publishes `name`; doctor renders it by that key."""
    assert make_result("WARN", "root", "x", label_key="name") == {
        "status": "WARN", "name": "root", "detail": "x",
    }


# ── summarize ─────────────────────────────────────────────────────────────────

def test_empty_results_are_clean():
    counts, exit_code = summarize([])
    assert counts == {"OK": 0, "WARN": 0, "FAIL": 0, "ERROR": 0}
    assert exit_code == 0


def test_warnings_alone_do_not_fail_the_verifier():
    counts, exit_code = summarize([make_result("WARN", "a", "d")])
    assert counts["WARN"] == 1
    assert exit_code == 0


def test_any_failure_sets_the_exit_code():
    counts, exit_code = summarize(
        [make_result("OK", "a", "d"), make_result("FAIL", "b", "d")]
    )
    assert counts == {"OK": 1, "WARN": 0, "FAIL": 1, "ERROR": 0}
    assert exit_code == 1


def test_counts_accumulate_per_status():
    results = [make_result(s, "x", "d") for s in ("OK", "OK", "WARN", "FAIL", "FAIL")]
    counts, exit_code = summarize(results)
    assert counts == {"OK": 2, "WARN": 1, "FAIL": 2, "ERROR": 0}
    assert exit_code == 1


def test_an_unknown_status_is_counted_rather_than_dropped():
    """A typo'd status silently vanishing from the totals is how a check stops
    reporting without anyone noticing — so it gets its own key instead."""
    counts, exit_code = summarize([make_result("SKIPPED", "a", "d")])
    assert counts["SKIPPED"] == 1
    assert exit_code == 0


def test_the_three_known_statuses_are_always_present_in_the_counts():
    """Consumers index counts['WARN'] directly; a missing key would KeyError."""
    counts, _ = summarize([])
    for status in ("OK", "WARN", "FAIL"):
        assert status in counts


# ── render ────────────────────────────────────────────────────────────────────

def test_render_matches_the_published_line_format():
    lines = render([make_result("FAIL", "pack.docs", "missing X")])
    assert lines == ["[FAIL] pack.docs: missing X"]


def test_render_honours_the_alternate_label_key():
    lines = render([make_result("OK", "root", "fine", label_key="name")],
                   label_key="name")
    assert lines == ["[OK] root: fine"]


# ── the "could not run" tier (audit finding S1, plan M3.1) ─────────────────────
#
# Before ERROR existed a verifier that could not read its own policy emitted a
# synthetic FAIL, which reads to every consumer as a measured verdict. summarize()
# yielded only 0 and 1, so "I ran and found nothing wrong" and "I never ran" were the
# same number.


def _row(status: str, label: str = "x"):
    return make_result(status, label, "detail")


def test_an_error_row_alone_exits_two_not_zero():
    """The whole point: a run that could not execute a check must not look clean."""
    counts, exit_code = summarize([_row("OK"), _row("ERROR")])
    assert exit_code == 2
    assert counts["ERROR"] == 1
    assert counts["FAIL"] == 0


def test_a_failure_outranks_an_error():
    """A run that both found a defect and failed to execute a check still has a defect
    to report, and callers that gate on `exit == 1` must keep seeing it."""
    counts, exit_code = summarize([_row("FAIL"), _row("ERROR")])
    assert exit_code == 1
    assert counts["FAIL"] == 1
    assert counts["ERROR"] == 1


def test_the_clean_case_is_still_zero():
    _, exit_code = summarize([_row("OK"), _row("WARN")])
    assert exit_code == 0


def test_error_is_a_declared_status_so_it_renders_and_counts():
    """An undeclared status still counts (summarize is defensive), but ERROR must be
    first-class: it appears in STATUSES, so every consumer that iterates the statuses
    reports it instead of silently omitting the column."""
    assert "ERROR" in STATUSES
    lines = render([_row("ERROR", "policy.json")])
    assert lines == ["[ERROR] policy.json: detail"]
