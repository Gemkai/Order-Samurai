"""Eval for the deterministic model-selector mechanism (bin/model_selector.py).

This IS the eval the LLM /model-selector skill never had: fixtures map session
signals (turns/errors/tools) to the expected complexity score and recommended
model, pin the band boundaries (30 -> sonnet, 70 -> opus), and assert idempotency
(same snapshot -> same selection).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.model_selector import (  # type: ignore[import-not-found]
    extract_signals,
    recommend_model,
    score_complexity,
    select,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _session(turns: int = 0, errors: int = 0, tools: int = 0) -> dict:
    """Build a session snapshot with `errors` error stop-reasons and `tools` tools."""
    return {
        "turn_count": turns,
        "stop_reasons": {str(i): "error" for i in range(errors)},
        "tools_used": {f"tool_{i}": 1 for i in range(tools)},
    }


# ---------------------------------------------------------------------------
# score_complexity
# ---------------------------------------------------------------------------

class ScoreComplexityTests(unittest.TestCase):

    def test_scores_minimum_for_trivial_session(self) -> None:
        # <5 turns (5) + no errors (0) + <=2 tools (0) = 5
        self.assertEqual(score_complexity(turns=1, errors=0, tools=1), 5)

    def test_scores_maximum_for_heavy_session(self) -> None:
        # >=30 turns (40) + >0.5 error rate (30) + >6 tools (30) = 100
        self.assertEqual(score_complexity(turns=40, errors=30, tools=10), 100)

    def test_clamps_score_at_one_hundred(self) -> None:
        self.assertLessEqual(score_complexity(turns=999, errors=999, tools=999), 100)

    def test_error_rate_uses_turns_as_denominator(self) -> None:
        # 2 errors over 20 turns = 0.1 rate -> 10 error points (not the <0.1 band)
        # 20 turns (35) + 10 + 0 tools = 45
        self.assertEqual(score_complexity(turns=20, errors=2, tools=0), 45)

    def test_handles_zero_turns_without_dividing_by_zero(self) -> None:
        self.assertEqual(score_complexity(turns=0, errors=0, tools=0), 5)


# ---------------------------------------------------------------------------
# recommend_model — band boundaries
# ---------------------------------------------------------------------------

class RecommendModelTests(unittest.TestCase):

    def test_recommends_haiku_below_thirty(self) -> None:
        self.assertEqual(recommend_model(29), "haiku")

    def test_recommends_sonnet_at_lower_boundary_thirty(self) -> None:
        self.assertEqual(recommend_model(30), "sonnet")

    def test_recommends_sonnet_just_below_seventy(self) -> None:
        self.assertEqual(recommend_model(69), "sonnet")

    def test_recommends_opus_at_lower_boundary_seventy(self) -> None:
        self.assertEqual(recommend_model(70), "opus")

    def test_recommends_opus_at_maximum(self) -> None:
        self.assertEqual(recommend_model(100), "opus")


# ---------------------------------------------------------------------------
# extract_signals
# ---------------------------------------------------------------------------

class ExtractSignalsTests(unittest.TestCase):

    def test_returns_zeros_for_empty_session(self) -> None:
        self.assertEqual(extract_signals({}), (0, 0, 0))

    def test_counts_only_error_stop_reasons(self) -> None:
        session = {"turn_count": 3, "stop_reasons": {"a": "error", "b": "end_turn"}}
        self.assertEqual(extract_signals(session), (3, 1, 0))

    def test_counts_distinct_tools(self) -> None:
        _, _, tools = extract_signals(_session(tools=4))
        self.assertEqual(tools, 4)

    def test_tolerates_null_collections(self) -> None:
        session = {"turn_count": 2, "stop_reasons": None, "tools_used": None}
        self.assertEqual(extract_signals(session), (2, 0, 0))


# ---------------------------------------------------------------------------
# select — end-to-end
# ---------------------------------------------------------------------------

class SelectTests(unittest.TestCase):

    def test_selects_haiku_for_simple_session(self) -> None:
        report = select(_session(turns=1, errors=0, tools=1))
        self.assertEqual(report["model"], "haiku")

    def test_selects_opus_for_complex_session(self) -> None:
        report = select(_session(turns=40, errors=30, tools=10))
        self.assertEqual(report["model"], "opus")

    def test_selects_sonnet_for_medium_session(self) -> None:
        report = select(_session(turns=20, errors=0, tools=3))  # 35 + 0 + 10 = 45
        self.assertEqual(report["model"], "sonnet")

    def test_report_carries_signal_breakdown(self) -> None:
        report = select(_session(turns=20, errors=2, tools=3))
        self.assertEqual(report["signals"], {"turns": 20, "errors": 2, "tools": 3})

    def test_report_reason_matches_chosen_model(self) -> None:
        report = select(_session(turns=1))
        self.assertIn("Haiku", report["reason"])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def test_same_session_yields_identical_selection(self) -> None:
        session = _session(turns=12, errors=1, tools=5)
        self.assertEqual(select(session), select(session))


if __name__ == "__main__":
    unittest.main()
