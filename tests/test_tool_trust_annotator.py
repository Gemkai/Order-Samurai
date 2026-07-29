"""Tool-trust annotation: the label must be a fact about the transcript, not a guess.

Every label here is decided from structure alone (no model). The properties worth pinning: reusing
a tool is not the same as retrying it, a repeat far later is NOT a retry, and sessions never bleed
into each other.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import tool_trust_annotator as ann  # type: ignore[import-not-found]
from agentica_core.evals.transcript_source import ToolUse, Turn


def _turn(*tool_uses):
    return Turn(input="do the thing", output="ok", tool_uses=list(tool_uses))


def _tu(name="Read", args=None, result="file contents", tid="t1"):
    return ToolUse(id=tid, name=name, input=args if args is not None else {"file_path": "a.py"}, result=result)


def _labels(session):
    return [r["label"] for r in ann.annotate_session(session)]


# --- label decisions -------------------------------------------------------------------------

def test_a_call_with_no_follow_up_is_trusted():
    assert _labels([_turn(_tu())]) == ["trusted"]


def test_same_tool_with_same_arguments_is_an_identical_retry():
    session = [_turn(_tu(), _tu())]
    assert _labels(session)[0] == "retried_identical"


def test_same_tool_with_different_arguments_is_reuse_not_correction():
    """Reading a second file is reading a second file. Labelling this 'corrected' put 74% of real
    tool calls in that bucket and drove the trust rate to a meaningless 23.6%."""
    session = [_turn(_tu(args={"file_path": "a.py"}), _tu(args={"file_path": "b.py"}))]
    assert _labels(session)[0] == "trusted"


def test_a_run_of_different_bash_commands_is_ordinary_work_not_correction():
    session = [_turn(*[_tu(name="Bash", args={"command": f"echo {i}"}) for i in range(5)])]
    assert set(_labels(session)) == {"trusted"}


def test_argument_key_order_does_not_hide_an_identical_retry():
    """{a,b} and {b,a} are the same call; sorting the key is what makes the retry detectable."""
    session = [_turn(
        _tu(args={"file_path": "a.py", "limit": 10}),
        _tu(args={"limit": 10, "file_path": "a.py"}),
    )]
    assert _labels(session)[0] == "retried_identical"


def test_an_errored_call_never_re_run_is_labelled_errored_no_retry():
    session = [_turn(_tu(result="<tool_use_error>no such file</tool_use_error>"))]
    assert _labels(session) == ["errored_no_retry"]


def test_an_errored_call_that_is_re_run_verbatim_is_an_identical_retry():
    session = [_turn(
        _tu(result="<tool_use_error>no such file</tool_use_error>"),
        _tu(result="contents"),
    )]
    assert _labels(session)[0] == "retried_identical"


def test_a_successful_call_followed_by_a_different_tool_stays_trusted():
    session = [_turn(_tu(name="Read"), _tu(name="Edit", args={"x": 1}))]
    assert _labels(session)[0] == "trusted"


def test_a_repeat_far_later_in_the_session_is_not_a_retry():
    """A repeat six calls later is a new decision, not a retry of this one."""
    filler = [_tu(name=f"Other{i}", args={"i": i}) for i in range(6)]
    session = [_turn(_tu(), *filler, _tu())]
    assert _labels(session)[0] == "trusted"


# --- error detection is narrow ---------------------------------------------------------------

def test_a_result_that_merely_discusses_failure_is_not_an_error():
    """A test report listing failures is not itself a tool error — the narrow-marker rule."""
    session = [_turn(_tu(result="Test summary: 3 failed, 12 passed"))]
    assert _labels(session) == ["trusted"]


def test_a_real_tool_error_marker_is_detected():
    session = [_turn(_tu(result="Error: command not found"))]
    assert _labels(session) == ["errored_no_retry"]


# --- aggregation -----------------------------------------------------------------------------

def _fake_sessions(sessions):
    def _iter(projects_dir=None, *, max_files=60):
        yield from sessions
    return _iter


def test_reports_a_gap_rather_than_a_perfect_score_when_no_tools_ran(monkeypatch):
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([]))
    payload = ann.run_annotator()

    assert payload["tool_calls"] == 0
    assert payload["trust_rate"] is None  # not 1.0 — no data is a gap, never a perfect score
    assert payload["retry_rate"] is None


def test_computes_trust_and_retry_rates_over_observed_calls(monkeypatch):
    session = [_turn(_tu(), _tu(), _tu(name="Edit", args={"x": 1}))]
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([session]))
    payload = ann.run_annotator()

    # call 1 retried_identical (call 2 repeats it), call 2 trusted (no later Read), call 3 trusted
    assert payload["tool_calls"] == 3
    assert payload["counts"]["retried_identical"] == 1
    assert payload["trust_rate"] == round(2 / 3, 3)
    assert payload["retry_rate"] == round(1 / 3, 3)


def test_declares_corrected_as_not_implemented(monkeypatch):
    """The removed proxy must stay declared, so its absence reads as a gap and not as 'no
    corrections ever happened'."""
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([[_turn(_tu())]]))
    assert "corrected" in ann.run_annotator()["not_implemented"]


def test_does_not_detect_a_retry_across_two_separate_sessions(monkeypatch):
    """Two sessions that each open with the same Read are not a retry."""
    a, b = [_turn(_tu())], [_turn(_tu())]
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([a, b]))
    payload = ann.run_annotator()

    assert payload["sessions_scanned"] == 2
    assert payload["counts"]["retried_identical"] == 0
    assert payload["trust_rate"] == 1.0


def test_declares_its_labels_heuristic_not_llm_judged(monkeypatch):
    """The honesty ladder distinction: nothing here was judged by a model."""
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([[_turn(_tu())]]))
    assert ann.run_annotator()["kind"] == "heuristic"


def test_declares_contradicted_as_not_implemented(monkeypatch):
    """The paper's fourth label needs judgment; its absence must be visible, not silent."""
    monkeypatch.setattr(ann, "iter_sessions", _fake_sessions([[_turn(_tu())]]))
    assert "contradicted" in ann.run_annotator()["not_implemented"]
