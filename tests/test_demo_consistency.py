"""Acceptance tests for public demo data consistency.

These tests exercise the validator as a public contract.  Each mutation changes
one otherwise-valid relationship, so any reported violation is attributable to
that relationship rather than to an unrelated malformed fixture.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_PATH = REPO_ROOT / "dashboard-ui" / "public" / "wid_payload.json"
VALIDATOR_PATH = REPO_ROOT / "demo" / "validate_payload.py"

def metric(value: str, *, percent: bool = False, **extra):
    return {"val": value, "is_percent": percent, **extra}


def validate(payload) -> list[str]:
    """Exercise the validator through its shipped command-line interface."""
    with tempfile.TemporaryDirectory() as temp_dir:
        payload_path = Path(temp_dir) / "wid_payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), str(payload_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    if result.returncode == 0:
        return []
    return [line for line in (result.stderr + result.stdout).splitlines() if line]


@pytest.fixture
def consistent_payload():
    """Small payload whose displayed facts all refer to the same sample run."""
    return {
        "window": {"days": 7, "records": 182, "successful_sessions": 166},
        "record_counts": {"claude": 164, "codex": 18},
        "pillars": {
            "bow": {
                "Activity": {
                    "Estimated_Agent_Time_Saved": metric("18.6"),
                    "Complexity_Weighted_Throughput": metric("2417"),
                }
            },
            "sword": {"Security": {"Kill_Chains_Disrupted": metric("6")}},
            "brush": {
                "Token Efficiency": {
                    "Total_Cost": metric("47.82"),
                    "Token_Spend": metric("18400000"),
                    "Token_Execution_Density": metric("110843.4"),
                    "Estimated_Cost_Savings": metric("312.40"),
                }
            },
            "arts": {
                "Docs": {"Doc_Parity_Issues": metric("4")},
                "Craft": {
                    "Craft_Improvements": metric("7"),
                    "Human_Hours_Saved": metric("12.4"),
                },
            },
        },
        "by_platform": {
            "claude": {
                "brush": {
                    "Token Efficiency": {
                        "Total_Cost": metric("47.82"),
                        "Estimated_Cost_Savings": metric("312.40"),
                    }
                }
            }
        },
        "category_scores": {
            "bow": {
                "graded_count": 1,
                "total_gradeable": 1,
                "flags": [],
                "rollup": {"worst": "PASS", "passing": 1, "graded": 1},
            },
            "sword": {
                "graded_count": 1,
                "total_gradeable": 1,
                "flags": [],
                "rollup": {"worst": "PASS", "passing": 1, "graded": 1},
            },
            "brush": {
                "graded_count": 1,
                "total_gradeable": 1,
                "flags": [],
                "rollup": {"worst": "PASS", "passing": 1, "graded": 1},
            },
            "arts": {
                "graded_count": 1,
                "total_gradeable": 1,
                "flags": [
                    {
                        "name": "Doc_Parity_Issues",
                        "val": "4",
                        "grade": "F",
                        "flagged": True,
                    }
                ],
                "rollup": {"worst": "CRITICAL", "passing": 0, "graded": 1},
            },
        },
        "needs_attention": {
            "count": 1,
            "items": [
                {
                    "metric": "Doc_Parity_Issues",
                    "pillar": "arts",
                    "status": "needs:human",
                    "val": "4",
                }
            ],
        },
        "reflexes": [
            {
                "id": "metric:arts:Doc_Parity_Issues",
                "tier": "CRITICAL",
                "source": "metric",
                "message": "Doc Parity Issues is at 4. Run /wiki on docs.",
            }
        ],
        "summaries": {
            "bow": (
                "This pillar tracked 18.6 hours returned across 182 work sessions "
                "and completed 2,417 complexity-weighted tasks."
            ),
            "brush": (
                "This pillar tracked $312.40 saved. The agent spent $47.82 in total, "
                "about $0.02 per task, using 18.4M tokens."
            ),
            "arts": (
                "This pillar logged 7 craft improvements and 4 extensions are out of "
                "date. Doc parity issues are now at 4."
            ),
        },
        "tier_mix": {},
    }


def assert_rejected(payload):
    violations = validate(payload)
    assert violations, "validator accepted a payload with a known contradiction"


def test_consistent_reference_payload_is_accepted(consistent_payload):
    assert validate(consistent_payload) == []


def test_arts_requires_measured_craft_improvements_headline(consistent_payload):
    del consistent_payload["pillars"]["arts"]["Craft"]["Craft_Improvements"]
    assert_rejected(consistent_payload)


def test_arts_does_not_require_retired_human_hours_saved(consistent_payload):
    del consistent_payload["pillars"]["arts"]["Craft"]["Human_Hours_Saved"]
    assert validate(consistent_payload) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("passing", 2), ("passing", -1), ("graded", -1), ("graded", 1.5)],
)
def test_rollup_counts_are_nonnegative_integers_and_passing_does_not_exceed_graded(
    consistent_payload, field, value
):
    consistent_payload["category_scores"]["bow"]["rollup"][field] = value
    assert_rejected(consistent_payload)


@pytest.mark.parametrize(
    ("pillar", "worst"),
    [("bow", "HIGH"), ("arts", "PASS")],
)
def test_rollup_worst_status_agrees_with_flagged_grades(
    consistent_payload, pillar, worst
):
    consistent_payload["category_scores"][pillar]["rollup"]["worst"] = worst
    assert_rejected(consistent_payload)


def test_attention_item_value_matches_referenced_metric(consistent_payload):
    consistent_payload["needs_attention"]["items"][0]["val"] = "914"
    assert_rejected(consistent_payload)


def test_reflex_message_value_matches_referenced_metric(consistent_payload):
    consistent_payload["reflexes"][0]["message"] = (
        "Doc Parity Issues is at 268. Run /wiki on docs."
    )
    assert_rejected(consistent_payload)


def test_token_execution_density_matches_token_spend_per_successful_session(
    consistent_payload,
):
    consistent_payload["pillars"]["brush"]["Token Efficiency"][
        "Token_Execution_Density"
    ]["val"] = "902326.3"
    assert_rejected(consistent_payload)


def test_declared_successful_session_denominator_must_be_positive(
    consistent_payload,
):
    consistent_payload["window"]["successful_sessions"] = 0
    assert_rejected(consistent_payload)


def test_legacy_payload_without_successful_session_count_skips_density_check(
    consistent_payload,
):
    del consistent_payload["window"]["successful_sessions"]
    consistent_payload["pillars"]["brush"]["Token Efficiency"][
        "Token_Execution_Density"
    ]["val"] = "902326.3"
    assert validate(consistent_payload) == []


@pytest.mark.parametrize(
    ("summary", "before", "after"),
    [
        ("arts", "Doc parity issues are now at 4", "Doc parity issues are now at 268"),
        ("arts", "logged 7 craft improvements", "logged 6 craft improvements"),
        ("brush", "$47.82 in total", "$999.00 in total"),
        ("brush", "using 18.4M tokens", "using 92.1M tokens"),
        ("bow", "2,417 complexity-weighted tasks", "9,999 complexity-weighted tasks"),
    ],
)
def test_narrative_values_match_displayed_metrics(
    consistent_payload, summary, before, after
):
    text = consistent_payload["summaries"][summary]
    assert before in text
    consistent_payload["summaries"][summary] = text.replace(before, after)
    assert_rejected(consistent_payload)


def test_public_demo_fixture_passes_all_consistency_requirements():
    payload = json.loads(PAYLOAD_PATH.read_text(encoding="utf-8"))
    assert validate(payload) == []
