"""Eval for the deterministic subagent-audit mechanism (bin/subagent_audit.py).

This IS the eval the LLM /subagent-audit skill never had: fixtures map spawn
records to expected verdicts, plus an idempotency check (same input → same report;
no spawns → empty wasteful list). The mechanism is read-only so idempotency holds
trivially — no state is mutated between calls.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.subagent_audit import (  # type: ignore[import-not-found]
    TOKEN_PREMIUM_K,
    classify_spawn,
    run_audit,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _spawn(
    description: str = "",
    prompt: str = "",
    turn_spawn_count: int = 1,
    session_id: str = "sess-001",
    tool_name: str = "Agent",
) -> dict:
    return {
        "session_id": session_id,
        "timestamp": "2026-06-15T00:00:00Z",
        "tool_name": tool_name,
        "description": description,
        "prompt": prompt,
        "turn_spawn_count": turn_spawn_count,
    }


def _session(session_id: str = "sess-001", agent_count: int = 0) -> dict:
    return {
        "session_id": session_id,
        "started_at": "2026-06-15T00:00:00Z",
        "turns": 10,
        "tools_used": {"Agent": agent_count, "Read": 5},
        "complexity": "medium",
        "top_files": [],
        "working_dir": "/tmp",
    }


_NOW = "2026-06-15T00:00:00+00:00"
_now_fn = lambda: _NOW


# ---------------------------------------------------------------------------
# ClassifySpawnTests
# ---------------------------------------------------------------------------

class ClassifySpawnTests(unittest.TestCase):

    def test_justified_parallel_when_three_or_more_spawns_in_turn(self) -> None:
        verdict, reason = classify_spawn(
            description="investigate module X",
            prompt="full module analysis",
            turn_spawn_count=3,
        )
        self.assertEqual(verdict, "justified_parallel")
        self.assertIn("3", reason)

    def test_three_trivial_spawns_are_not_classified_parallel(self) -> None:
        # SA-01 fix: 3+ trivial spawns must NOT get justified_parallel — they are wasteful.
        verdict, reason = classify_spawn(
            description="find the config file",
            prompt="look for it",
            turn_spawn_count=3,
        )
        self.assertEqual(verdict, "wasteful_trivial")
        self.assertIn("find", reason)

    def test_justified_isolation_when_review_keyword_in_description(self) -> None:
        verdict, reason = classify_spawn(
            description="code review of auth module",
            prompt="please review this code",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "justified_isolation")
        self.assertIn("review", reason)

    def test_justified_isolation_when_security_keyword_in_prompt(self) -> None:
        verdict, reason = classify_spawn(
            description="check module",
            prompt="perform a security audit of this endpoint",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "justified_isolation")
        # prompt contains "security" — assert the isolation family fired
        self.assertIn("isolation keyword", reason)

    def test_spawn_with_audit_in_description_is_not_auto_justified(self) -> None:
        # SA-02 fix: "audit" alone is no longer an ISOLATION_KEYWORDS match.
        # A "token audit" or "cost audit" spawn must not escape waste detection.
        verdict, _ = classify_spawn(
            description="run token audit",
            prompt="count tokens",
            turn_spawn_count=1,
        )
        # No isolation/fanout keyword, no trivial keyword in desc → wasteful_serial
        self.assertNotEqual(verdict, "justified_isolation")

    def test_justified_fanout_when_prompt_exceeds_800_chars(self) -> None:
        long_prompt = "a" * 801
        verdict, reason = classify_spawn(
            description="process items",
            prompt=long_prompt,
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "justified_fanout")
        self.assertIn("large prompt", reason)

    def test_justified_fanout_when_fanout_keyword_in_description(self) -> None:
        verdict, reason = classify_spawn(
            description="bulk migration of all files",
            prompt="short prompt",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "justified_fanout")

    def test_wasteful_trivial_when_read_keyword_in_description(self) -> None:
        verdict, reason = classify_spawn(
            description="read the config file",
            prompt="read it",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "wasteful_trivial")
        self.assertIn("read", reason)

    def test_wasteful_trivial_when_find_keyword_in_description(self) -> None:
        verdict, reason = classify_spawn(
            description="find the error in log",
            prompt="look in the logs",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "wasteful_trivial")

    def test_wasteful_serial_when_no_pattern_matched_and_single_spawn(self) -> None:
        verdict, reason = classify_spawn(
            description="update the version number",
            prompt="change 1.0 to 2.0",
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "wasteful_serial")
        self.assertIn("single/paired", reason)

    def test_unknown_when_no_rule_fires_with_multi_spawn(self) -> None:
        # turn_spawn_count=2 means parallel rule needs >=3; no keywords match either
        verdict, reason = classify_spawn(
            description="update the version number",
            prompt="change 1.0 to 2.0",
            turn_spawn_count=2,
        )
        # 2 spawns: not >=3 (parallel), no isolation/fanout/trivial keyword → falls to
        # "wasteful_serial" because turn_spawn_count <= 2
        # Per the rules: if turn_spawn_count <= 2 after all other checks fail → wasteful_serial
        # The "unknown" branch only triggers when turn_spawn_count > 2 and no rule matched.
        self.assertEqual(verdict, "wasteful_serial")

    def test_unknown_verdict_when_above_two_spawns_but_below_three_is_impossible(self) -> None:
        # Confirm: the only path to "unknown" requires turn_spawn_count > 2 (i.e. >=3),
        # but >=3 is already caught by the parallel rule first. So "unknown" requires
        # the caller to pass a turn_spawn_count > 2 that somehow also isn't >=3 — impossible
        # in practice. We verify the parallel rule fires at exactly 3.
        verdict, _ = classify_spawn(
            description="update the version number",
            prompt="change 1.0 to 2.0",
            turn_spawn_count=3,
        )
        self.assertEqual(verdict, "justified_parallel")

    def test_isolation_keyword_in_prompt_first_500_chars_matches(self) -> None:
        prompt = "adversarial " + "x" * 20
        verdict, reason = classify_spawn(
            description="something generic",
            prompt=prompt,
            turn_spawn_count=1,
        )
        self.assertEqual(verdict, "justified_isolation")
        self.assertIn("adversarial", reason)

    def test_isolation_keyword_beyond_500_chars_in_prompt_does_not_match(self) -> None:
        # adversarial keyword placed after 500 chars in prompt — should NOT trigger isolation
        prompt = "x" * 501 + " adversarial review "
        # Also not trivial/fanout in description, turn_spawn_count=1 → wasteful_serial
        verdict, _ = classify_spawn(
            description="update the version number",
            prompt=prompt,
            turn_spawn_count=1,
        )
        # prompt len > 800? 501 + 20 = 521 < 800, so not fanout by length.
        # No fanout keyword in combined (desc + prompt[:500]).
        # No trivial keyword in desc_low.
        # turn_spawn_count <= 2 → wasteful_serial
        self.assertEqual(verdict, "wasteful_serial")


# ---------------------------------------------------------------------------
# RunAuditTests
# ---------------------------------------------------------------------------

class RunAuditTests(unittest.TestCase):

    def test_counts_sessions_and_spawns_correctly(self) -> None:
        sessions = [_session("s1"), _session("s2"), _session("s3")]
        spawns = [
            _spawn("read the file", session_id="s1"),
            _spawn("code review", session_id="s2"),
        ]
        report = run_audit(
            session_files_fn=lambda: sessions,
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(report["sessions_analyzed"], 3)
        self.assertEqual(report["spawns_analyzed"], 2)
        self.assertEqual(report["counts"]["sessions"], 3)
        self.assertEqual(report["counts"]["spawns"], 2)

    def test_classifies_wasteful_and_justified_from_fixtures(self) -> None:
        spawns = [
            _spawn("read the config file", prompt="read it", turn_spawn_count=1),
            _spawn("code review of PR", prompt="please review", turn_spawn_count=1),
        ]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(report["counts"]["wasteful"], 1)
        self.assertEqual(report["counts"]["justified"], 1)
        wasteful_verdict = report["wasteful"][0]["verdict"]
        self.assertEqual(wasteful_verdict, "wasteful_trivial")
        justified_verdict = report["justified"][0]["verdict"]
        self.assertEqual(justified_verdict, "justified_isolation")

    def test_computes_total_recoverable_k_from_wasteful_spawns(self) -> None:
        # Two wasteful spawns → 2 × TOKEN_PREMIUM_K
        spawns = [
            _spawn("read the log", prompt="read it", turn_spawn_count=1),
            _spawn("find the error", prompt="look here", turn_spawn_count=1),
        ]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(report["total_recoverable_k"], 2 * TOKEN_PREMIUM_K)

    def test_token_premium_k_is_set_on_each_wasteful_row(self) -> None:
        spawns = [_spawn("read the file", turn_spawn_count=1)]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(report["wasteful"][0]["token_premium_k"], TOKEN_PREMIUM_K)

    def test_top_wasteful_pattern_reflects_most_common_verdict(self) -> None:
        spawns = [
            _spawn("read the file", turn_spawn_count=1),
            _spawn("find the error", turn_spawn_count=1),
            _spawn("update version", prompt="bump it", turn_spawn_count=1),
        ]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        # 2 × wasteful_trivial (read, find), 1 × wasteful_serial (update)
        self.assertEqual(report["top_wasteful_pattern"], "wasteful_trivial")

    def test_top_wasteful_pattern_is_none_when_no_wasteful_spawns(self) -> None:
        spawns = [_spawn("code review", prompt="review this", turn_spawn_count=1)]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(report["top_wasteful_pattern"], "none")

    def test_n_sessions_caps_sessions_analyzed(self) -> None:
        sessions = [_session(f"s{i}") for i in range(10)]
        report = run_audit(
            n_sessions=5,
            session_files_fn=lambda: sessions,
            transcript_spawns_fn=lambda: [],
            now_fn=_now_fn,
        )
        self.assertEqual(report["sessions_analyzed"], 5)

    def test_generated_at_uses_now_fn(self) -> None:
        report = run_audit(
            session_files_fn=lambda: [],
            transcript_spawns_fn=lambda: [],
            now_fn=_now_fn,
        )
        self.assertEqual(report["generated_at"], _NOW)

    def test_justified_rows_do_not_have_token_premium_k(self) -> None:
        spawns = [_spawn("code review", prompt="review this", turn_spawn_count=1)]
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertNotIn("token_premium_k", report["justified"][0])


# ---------------------------------------------------------------------------
# IdempotencyTests
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def test_same_input_yields_identical_report(self) -> None:
        sessions = [_session("s1"), _session("s2")]
        spawns = [
            _spawn("read the config", turn_spawn_count=1),
            _spawn("code review of auth", turn_spawn_count=1),
            _spawn("bulk migration of all files", turn_spawn_count=1),
        ]
        first = run_audit(
            session_files_fn=lambda: sessions,
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        second = run_audit(
            session_files_fn=lambda: sessions,
            transcript_spawns_fn=lambda: spawns,
            now_fn=_now_fn,
        )
        self.assertEqual(first, second)

    def test_no_spawns_produces_empty_wasteful_list(self) -> None:
        report = run_audit(
            session_files_fn=lambda: [_session()],
            transcript_spawns_fn=lambda: [],
            now_fn=_now_fn,
        )
        self.assertEqual(report["wasteful"], [])
        self.assertEqual(report["total_recoverable_k"], 0)
        self.assertEqual(report["top_wasteful_pattern"], "none")
        self.assertEqual(report["spawns_analyzed"], 0)

    def test_empty_sessions_and_empty_spawns_produces_zeroed_report(self) -> None:
        report = run_audit(
            session_files_fn=lambda: [],
            transcript_spawns_fn=lambda: [],
            now_fn=_now_fn,
        )
        self.assertEqual(report["sessions_analyzed"], 0)
        self.assertEqual(report["spawns_analyzed"], 0)
        self.assertEqual(report["justified"], [])
        self.assertEqual(report["wasteful"], [])
        self.assertEqual(report["unknown"], [])
        self.assertEqual(report["total_recoverable_k"], 0)


if __name__ == "__main__":
    unittest.main()
