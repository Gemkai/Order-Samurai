"""run_all's contract: one broken verifier must never abort the whole doctor.

The module docstring promises "a verifier that raises becomes a single FAIL
result, never a total abort." A verifier that returns a non-iterable (the very
common `return None` / fall-off-the-end mistake) used to slip through, because
only the *call* was wrapped in try/except — the `for raw in raw_results` that
consumes the return value was not. `for raw in None` raises TypeError and
aborts run_all, blinding every remaining check.
"""
from agentica_core import verifiers


def _ok(label):
    return lambda: [{"status": "OK", "label": label, "detail": ""}]


def test_verifier_returning_none_is_isolated_as_fail_not_abort():
    results = verifiers.run_all([lambda: None, _ok("downstream-check")])
    statuses = [r["status"] for r in results]
    # The downstream verifier must still have run despite the bad one.
    assert "OK" in statuses
    # The None-returning verifier is isolated as a single FAIL.
    assert any(r["status"] == "FAIL" for r in results)


def test_verifier_returning_bare_dict_is_isolated_as_fail_not_abort():
    # A bare dict is iterable over its KEYS — previously produced bogus per-key
    # "malformed" FAILs; it must instead be one clear FAIL, and not blind the rest.
    bad = lambda: {"status": "OK", "label": "x", "detail": ""}
    results = verifiers.run_all([bad, _ok("downstream-check")])
    assert "OK" in [r["status"] for r in results]
    fails = [r for r in results if r["status"] == "FAIL"]
    assert len(fails) == 1


def test_all_valid_verifiers_pass_through_unchanged():
    results = verifiers.run_all([_ok("a"), _ok("b")])
    assert [r["status"] for r in results] == ["OK", "OK"]
