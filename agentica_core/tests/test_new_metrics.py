"""Tests for metrics added recently with no dedicated coverage:
Gate_Canary_Fault, Local_Routing_Share, Avg_Session_Turns (graded),
Cache_Hit_Rate. (Canary_Failures retired 2026-07-11; Loop_Breaker_Fires
retired 2026-07-19 — dead emitter, metric-surface review Part E item 3.)
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from agentica_core import aggregate as agg, scouts
from agentica_core.insights import _health


# ── helpers ──────────────────────────────────────────────────────────────────

def _sig(tmp_path, **files):
    """Write data files and return security_signals(tmp_path)."""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(json.dumps(content), encoding="utf-8")
    return scouts.security_signals(tmp_path)


# ── Local_Routing_Share ───────────────────────────────────────────────────────

class TestLocalRoutingShare:
    def test_mixed_tiers_returns_correct_percentage(self):
        recs = [
            {"model_tier": "LOCAL"},
            {"model_tier": "LOCAL"},
            {"model_tier": "FAST"},
            {"model_tier": "FREE"},
        ]
        assert agg.r_local_routing(recs) == 50.0

    def test_all_local_returns_100(self):
        recs = [{"model_tier": "LOCAL"}, {"model_tier": "local"}]
        assert agg.r_local_routing(recs) == 100.0

    def test_no_local_returns_0(self):
        recs = [{"model_tier": "FAST"}, {"model_tier": "FREE"}]
        assert agg.r_local_routing(recs) == 0.0

    def test_empty_records_returns_none(self):
        assert agg.r_local_routing([]) is None

    def test_records_without_model_tier_returns_none(self):
        assert agg.r_local_routing([{"status": "success"}]) is None

    def test_local_tier_case_insensitive(self):
        recs = [{"model_tier": "local"}, {"model_tier": "FAST"}]
        assert agg.r_local_routing(recs) == 50.0


# ── Avg_Session_Turns ─────────────────────────────────────────────────────────

class TestAvgSessionTurns:
    def test_turns_field_wins_over_row_counting(self):
        # Emitter-stamped turns are the real measurement; row counts are re-emits.
        recs = [
            {"session_id": "s1", "turns": 6}, {"session_id": "s1", "turns": 6},
            {"session_id": "s2", "turns": 2},
        ]
        # s1=6 turns (once, not per row), s2=2 → avg=4.0
        assert agg.r_avg_session_turns(recs) == 4.0

    def test_turns_is_cumulative_per_session_so_max_wins(self):
        # A resumed session re-emits the same sid with a higher cumulative count.
        recs = [{"session_id": "s1", "turns": 3}, {"session_id": "s1", "turns": 9}]
        assert agg.r_avg_session_turns(recs) == 9.0

    def test_zero_turns_means_not_parsed_and_is_not_graded(self):
        # turns=0 is "transcript unreadable", never a real zero-turn session.
        recs = [{"session_id": "s1", "turns": 0}, {"session_id": "s2", "turns": 5}]
        assert agg.r_avg_session_turns(recs) == 5.0

    def test_placeholder_sid_turns_count_individually(self):
        recs = [
            {"session_id": "local-session", "turns": 2},
            {"session_id": "local-session", "turns": 4},
        ]
        # placeholder sids never collapse into one session → avg of both
        assert agg.r_avg_session_turns(recs) == 3.0

    def test_two_sessions_without_turns_fall_back_to_row_proxy(self):
        recs = [
            {"session_id": "s1"}, {"session_id": "s1"}, {"session_id": "s1"},
            {"session_id": "s2"}, {"session_id": "s2"},
        ]
        # documented proxy: rows per session (claude re-emit count) — s1=3, s2=2 → 2.5
        assert agg.r_avg_session_turns(recs) == 2.5

    def test_single_session_without_turns_returns_row_count(self):
        recs = [{"session_id": "s1"}] * 4
        assert agg.r_avg_session_turns(recs) == 4.0

    def test_empty_records_returns_none(self):
        assert agg.r_avg_session_turns([]) is None

    def test_records_without_session_id_returns_none(self):
        assert agg.r_avg_session_turns([{"status": "success"}]) is None

    def test_graded_healthy_below_warn(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        # 5 turns → well below warn=8 → should score 100
        assert _health(5.0, rule) == 100.0

    def test_graded_at_fail_threshold(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        # 15 turns → at fail=15 → should score ~40
        score = _health(15.0, rule)
        assert score == pytest.approx(40.0, abs=1.0)

    def test_graded_between_warn_and_fail(self):
        rule = {"dir": "lower", "warn": 8, "fail": 15}
        score = _health(11.5, rule)  # midpoint
        assert 40.0 < score < 100.0


# Canary_Failures RETIRED 2026-07-11 — scout reader removed with the metric.


# ── Gate_Canary_Fault ─────────────────────────────────────────────────────────

class TestGateCanaryFault:
    def test_missing_file_absent_from_output(self, tmp_path):
        sig = scouts.security_signals(tmp_path)
        assert "gate_canary_fault" not in sig

    def test_working_fresh_canary_returns_0(self, tmp_path):
        now = datetime.now(timezone.utc).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": now,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0

    def test_gate_not_working_returns_1(self, tmp_path):
        now = datetime.now(timezone.utc).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": False,
                "last_run": now,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 1

    def test_stale_canary_returns_1(self, tmp_path):
        stale = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": stale,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 1

    def test_fresh_within_budget_returns_0(self, tmp_path):
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": recent,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0

    def test_exactly_at_budget_boundary_passes(self, tmp_path):
        # days > max_age triggers fault; days == max_age does not
        exact = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        sig = _sig(tmp_path, **{
            "security_gate_canary.json": {
                "gate_working": True,
                "last_run": exact,
                "max_age_days": 7,
            }
        })
        assert sig["gate_canary_fault"] == 0


# Loop_Breaker_Fires tests removed 2026-07-19 with the metric's retirement
# (dead emitter — see agentica_core/insights.py retirement comment).


# ── False-zero prevention (Task 2 bug fixes) ──────────────────────────────────

class TestReducerFalseZero:
    def test_total_cost_returns_none_when_field_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_total_cost(recs) is None

    def test_token_spend_returns_none_when_fields_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_token_spend(recs) is None

    def test_cost_per_task_returns_none_when_cost_absent(self):
        recs = [{"status": "success"}, {"status": "error"}]
        assert agg.r_cost_per_task(recs) is None

    def test_total_cost_returns_value_when_present(self):
        recs = [{"total_cost": 0.01}, {"total_cost": 0.02}]
        assert agg.r_total_cost(recs) == pytest.approx(0.03, abs=1e-6)

    def test_token_spend_returns_value_when_present(self):
        recs = [{"tokens_prompt": 100, "tokens_completion": 50}]
        assert agg.r_token_spend(recs) == 150


# ── Lesson_Graduation_Rate (AUTO-017) ─────────────────────────────────────────

def _queue_jsonl(tmp_path, skills):
    data_dir = tmp_path / ".claude" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "skill_improve_queue.jsonl"
    path.write_text(
        "\n".join(json.dumps({"skill": s, "used_at": "2026-07-01T00:00:00Z",
                              "improve_after": "2026-07-02T00:00:00Z"}) for s in skills) + "\n",
        encoding="utf-8",
    )
    return path


def _eureka_md(tmp_path, rule_skills=(), body=""):
    data_dir = tmp_path / ".claude" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "auto_eureka_skills.md"
    rule_lines = "\n".join(f"- **`/{s}`** → `bow/Some_Metric`\n  6/8 runs improved (75%)" for s in rule_skills)
    md = f"""# Auto Eureka

## GOTCHA — Skills That Rarely Resolve Their Target Metric

- something unrelated

## RULE — High-Effectiveness Skills

{rule_lines}

## CONTEXT — Mixed Effectiveness (30–70%)

{body}
"""
    path.write_text(md, encoding="utf-8")
    return path


class TestLessonGraduationRate:
    def test_missing_queue_file_returns_data_gap_not_fake_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = agg._lesson_graduation_rate([])
        assert result["val"] is None
        assert result["data_gap"] is True
        assert result["calibrated"] is True

    def test_empty_queue_returns_data_gap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, [])
        result = agg._lesson_graduation_rate([])
        assert result["val"] is None
        assert result["data_gap"] is True

    def test_missing_eureka_report_treats_all_as_ungraduated(self, tmp_path, monkeypatch):
        # Ledger has lessons, but the classification report hasn't run yet: 0%,
        # never a crash, never a fabricated non-zero.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["simplify", "doctor"])
        result = agg._lesson_graduation_rate([])
        assert result["val"] == 0.0
        assert result["calibrated"] is True
        assert "data_gap" not in result

    def test_computes_real_percentage_from_both_sources(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["simplify", "audit-mechanisms", "doctor", "review"])
        _eureka_md(tmp_path, rule_skills=["simplify", "audit-mechanisms"])
        result = agg._lesson_graduation_rate([])
        # 2 of 4 queued skills graduated to RULE = 50%
        assert result["val"] == pytest.approx(50.0, abs=0.05)
        assert result["calibrated"] is True
        assert "data_gap" not in result

    def test_rule_skill_never_queued_does_not_count(self, tmp_path, monkeypatch):
        # A RULE-classified skill that was never in THIS ledger must not inflate
        # the rate — graduation is scoped to the ledger's own population.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["doctor", "review"])
        _eureka_md(tmp_path, rule_skills=["simplify"])
        result = agg._lesson_graduation_rate([])
        assert result["val"] == 0.0

    def test_duplicate_queue_entries_dedupe_by_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["simplify", "simplify", "simplify", "doctor"])
        _eureka_md(tmp_path, rule_skills=["simplify"])
        result = agg._lesson_graduation_rate([])
        # 1 of 2 unique queued skills graduated = 50%, not diluted by repeats
        assert result["val"] == pytest.approx(50.0, abs=0.05)

    def test_ignores_records_argument_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["simplify"])
        _eureka_md(tmp_path, rule_skills=["simplify"])
        fake_records = [{"total_cost": 999.0, "model_tier": "CLOUD"}] * 50
        result = agg._lesson_graduation_rate(fake_records)
        assert result["val"] == pytest.approx(100.0, abs=0.05)

    def test_registered_in_registry_under_bow_as_auto_percent(self):
        entry = next((e for e in agg.REGISTRY if e[2] == "Lesson_Graduation_Rate"), None)
        assert entry is not None, "Lesson_Graduation_Rate must be registered in aggregate.REGISTRY"
        pillar, group, key, reducer, tier, is_percent, is_count = entry
        assert pillar == "bow"
        assert tier == "AUTO"
        assert is_percent is True
        assert is_count is False
        assert reducer is agg._lesson_graduation_rate

    def test_build_pillars_reports_live_not_simulated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        _queue_jsonl(tmp_path, ["simplify", "doctor"])
        _eureka_md(tmp_path, rule_skills=["simplify"])
        pillars = agg.build_pillars([])
        env = pillars["bow"]["Agent Operation"]["Lesson_Graduation_Rate"]
        assert env["is_simulated"] is False
        assert env["tier"] == "AUTO"
        assert env["val"] != "—"


# ── Cache_Hit_Rate (AUTO-009) ─────────────────────────────────────────────────
# Unlike the DERIVED reducers above, _cache_hit_rate ignores the `records` arg
# entirely — cache_read_input_tokens / cache_creation_input_tokens only exist on
# the raw transcript's message.usage block, never on the SessionEnd telemetry
# record. So these tests point HOME/USERPROFILE at a tmp_path and write fake
# transcript JSONLs, mirroring how a real ~/.claude/projects/**/*.jsonl looks.

def _write_transcript(projects_dir, name, lines):
    proj = projects_dir / "proj1"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / name
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    return path


def _usage_line(input_tokens=0, cache_creation=0, cache_read=0, output_tokens=0):
    return {
        "type": "assistant",
        "message": {"usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output_tokens,
        }},
    }


class TestCacheHitRate:
    def setup_method(self):
        # Every test starts with a cold cache so a prior test's tmp_path result
        # can't leak in via the TTL.
        agg._CACHE_HIT_CACHE.update(t=0.0, v=None)

    def teardown_method(self):
        agg._CACHE_HIT_CACHE.update(t=0.0, v=None)

    def test_missing_projects_dir_returns_data_gap_not_fake_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        result = agg._cache_hit_rate([])
        assert result["val"] is None
        assert result["data_gap"] is True
        assert result["calibrated"] is True

    def test_projects_dir_with_no_usage_blocks_returns_data_gap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        _write_transcript(projects_dir, "s1.jsonl", [{"type": "user", "message": {}}])
        result = agg._cache_hit_rate([])
        assert result["val"] is None
        assert result["data_gap"] is True

    def test_computes_real_percentage_from_usage_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        _write_transcript(projects_dir, "s1.jsonl", [
            _usage_line(input_tokens=2, cache_creation=100, cache_read=900, output_tokens=5),
        ])
        result = agg._cache_hit_rate([])
        # 900 / (900 + 100 + 2) = 89.8%
        assert result["val"] == pytest.approx(89.8, abs=0.05)
        assert result["calibrated"] is True
        assert "data_gap" not in result

    def test_sums_across_multiple_lines_and_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        _write_transcript(projects_dir, "s1.jsonl", [
            _usage_line(input_tokens=0, cache_creation=0, cache_read=100),
            _usage_line(input_tokens=0, cache_creation=100, cache_read=0),
        ])
        result = agg._cache_hit_rate([])
        # 100 / (100 + 100 + 0) = 50%
        assert result["val"] == pytest.approx(50.0, abs=0.05)

    def test_ignores_non_assistant_and_malformed_lines(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        proj = projects_dir / "proj1"
        proj.mkdir(parents=True)
        path = proj / "s1.jsonl"
        good = json.dumps(_usage_line(input_tokens=1, cache_creation=0, cache_read=99))
        user_line = json.dumps({"type": "user", "message": {"usage": {"cache_read_input_tokens": 99999}}})
        path.write_text(good + "\n" + user_line + "\nnot valid json\n", encoding="utf-8")
        result = agg._cache_hit_rate([])
        # Only the assistant line's usage counts — the user line's inflated
        # cache_read_input_tokens must NOT leak in, and the garbage line must not crash it.
        assert result["val"] == pytest.approx(99.0, abs=0.05)

    def test_ignores_records_argument_entirely(self, tmp_path, monkeypatch):
        # The reducer signature accepts `records` (REGISTRY calls fn(records) uniformly)
        # but this metric's real source is the transcript files, not telemetry records.
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        _write_transcript(projects_dir, "s1.jsonl", [
            _usage_line(input_tokens=1, cache_creation=0, cache_read=9),
        ])
        fake_records = [{"total_cost": 999.0, "model_tier": "CLOUD"}] * 50
        result = agg._cache_hit_rate(fake_records)
        assert result["val"] == pytest.approx(90.0, abs=0.05)

    def test_registered_in_registry_under_brush_as_auto_percent(self):
        entry = next((e for e in agg.REGISTRY if e[2] == "Cache_Hit_Rate"), None)
        assert entry is not None, "Cache_Hit_Rate must be registered in aggregate.REGISTRY"
        pillar, group, key, reducer, tier, is_percent, is_count = entry
        assert pillar == "brush"
        assert tier == "AUTO"
        assert is_percent is True
        assert is_count is False
        assert reducer is agg._cache_hit_rate

    def test_build_pillars_reports_live_not_simulated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        projects_dir = tmp_path / ".claude" / "projects"
        _write_transcript(projects_dir, "s1.jsonl", [
            _usage_line(input_tokens=1, cache_creation=1, cache_read=98),
        ])
        pillars = agg.build_pillars([])
        env = pillars["brush"]["Token Efficiency"]["Cache_Hit_Rate"]
        assert env["is_simulated"] is False
        assert env["tier"] == "AUTO"
        assert env["val"] != "—"  # not the "—" no-data placeholder
