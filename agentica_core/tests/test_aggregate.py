import json

from agentica_core import aggregate as agg
from agentica_core.telemetry import validate_metric

RECS = [
    {"status": "success", "latency_ms": 10.0, "tokens_prompt": 100, "tokens_completion": 50,
     "total_cost": 0.01, "tool_calls": 2, "tool_calls_list": ["a", "b"], "session_id": "s1",
     "mod_type": "SURGICAL", "model_tier": "FAST"},
    {"status": "error", "latency_ms": 30.0, "tokens_prompt": 200, "tokens_completion": 20,
     "total_cost": 0.02, "tool_calls": 1, "tool_calls_list": ["a"], "session_id": "s1",
     "mod_type": "CLOBBER", "model_tier": "FAST"},
    {"status": "success", "latency_ms": 20.0, "tokens_prompt": 50, "tokens_completion": 10,
     "total_cost": 0.005, "tool_calls": 0, "tool_calls_list": [], "session_id": "s2",
     "mod_type": "READ", "model_tier": "FREE"},
]


def test_live_metric_values():
    p = agg.build_pillars(RECS)
    assert p["bow"]["Activity"]["Complexity_Weighted_Throughput"]["val"] == "3.1"
    # Error_Rate on 3 records is uncalibrated (< MIN_ERROR_SAMPLE) — graded path is covered
    # in test_error_rate_min_sample_guard.
    assert p["bow"]["Activity"]["Error_Rate"]["is_simulated"] is True
    assert p["bow"]["Activity"]["Session_Count"]["val"] == "2"
    assert p["brush"]["Token Efficiency"]["Total_Cost"]["val"] == "0.035"
    assert p["brush"]["Token Efficiency"]["Token_Spend"]["val"] == "430"
    assert p["brush"]["Code Health"]["Revision_Ratio"]["val"] == "50.0"  # 1 CLOBBER of 2 mods


def test_error_rate_min_sample_guard():
    # Fewer than MIN_ERROR_SAMPLE records -> uncalibrated (None), not a false FAIL on noise.
    assert agg.r_error_rate([{"status": "error"}, {"status": "success"}]) is None

    # At/above the sample floor -> graded. 2 errors of 10 = 20.0%.
    big = [{"status": "error"}] * 2 + [{"status": "success"}] * 8
    assert agg.r_error_rate(big) == 20.0
    assert agg.error_rate_stats(big) == (20.0, 2, 10)


def test_live_metrics_not_simulated():
    p = agg.build_pillars(RECS)
    assert p["bow"]["Activity"]["Complexity_Weighted_Throughput"]["is_simulated"] is False
    assert p["bow"]["Activity"]["Complexity_Weighted_Throughput"]["tier"] == "DERIVED"


def test_revision_ratio_read_only_is_live_zero():
    p = agg.build_pillars([{"status": "success", "mod_type": "READ"}])
    env = p["brush"]["Code Health"]["Revision_Ratio"]
    assert env["val"] == "0.0"
    assert env["is_simulated"] is False
    assert env["tier"] == "DERIVED"


def test_empty_records_all_telemetry_simulated():
    p = agg.build_pillars([])
    tput = p["bow"]["Activity"]["Complexity_Weighted_Throughput"]
    assert tput["is_simulated"] is True
    assert tput["tier"] == "SIMULATED"
    assert tput["val"] == "—"


def test_every_envelope_is_contract_valid():
    p = agg.build_pillars(RECS)
    for pillar in p.values():
        for group in pillar.values():
            for env in group.values():
                validate_metric(env)  # must not raise


def test_aggregate_payload_shape_cross_platform():
    payload = agg.aggregate()
    assert set(payload["pillars"]) == {"bow", "sword", "brush", "arts"}
    assert "by_platform" in payload and "record_counts" in payload
    assert "claude" in payload["platforms"] and "codex" in payload["platforms"]
    assert "claude" in payload["record_counts"]  # populated once the Claude emitter hook runs


def test_secrets_metric_injected_live():
    payload = agg.aggregate()
    secrets = payload["pillars"]["sword"]["Code Security"]["Secrets_Detected"]
    assert secrets["tier"] == "AUTO"
    assert secrets["is_simulated"] is False


def test_platform_metrics_live_iff_records():
    payload = agg.aggregate()
    # a platform's telemetry-derived metrics are live exactly when it has records (tier honesty)
    for p, counts in payload["record_counts"].items():
        sim = payload["by_platform"][p]["bow"]["Activity"]["Complexity_Weighted_Throughput"]["is_simulated"]
        assert sim == (counts == 0), f"{p}: {counts} records but simulated={sim}"


def test_verifier_derived_metrics_are_real():
    payload = agg.aggregate()
    bow = payload["pillars"]["bow"]
    assert bow["Governance"]["Governance_Pass_Rate"]["is_simulated"] is False
    assert bow["Governance"]["Governance_Pass_Rate"]["tier"] == "AUTO"
    # Boundary_Violations was SIMULATED in the registry; verifier data makes it real
    bv = payload["pillars"]["sword"]["Code Security"]["Boundary_Violations"]
    assert bv["is_simulated"] is False and bv["tier"] == "AUTO"


def test_platform_independent_signals_not_summed():
    """The doc-parity scout runs the same platform-independent check for every
    platform — the all-platform value must equal the per-platform value, never
    the sum across platforms (10 was showing as 30)."""
    payload = agg.aggregate()
    combined = payload["pillars"]["arts"]["Docs"]["Doc_Parity_Issues"]
    per = [payload["by_platform"][p]["arts"]["Docs"]["Doc_Parity_Issues"]
           for p in payload["platforms"]]
    live_vals = [float(e["val"]) for e in per if not e["is_simulated"]]
    if not combined["is_simulated"] and live_vals:
        assert float(combined["val"]) == live_vals[0]


def test_process_scout_metric_present():
    payload = agg.aggregate()
    apc = payload["pillars"]["bow"]["Autonomic"]["Agent_Process_Count"]
    assert apc["tier"] == "AUTO" and apc["is_simulated"] is False


def test_security_signals_reads_existing_logs(tmp_path):
    from agentica_core import scouts
    d = tmp_path / "data"
    d.mkdir()
    (d / "principle_violations.jsonl").write_text('{"rule_id":"x"}\n{"rule_id":"y"}\n', encoding="utf-8")
    (d / "canary_status.json").write_text('{"failed":2}', encoding="utf-8")
    (d / "mechanism_audit.json").write_text('{"counts":{"orphan":1,"critical":0}}', encoding="utf-8")
    (d / "dependency_audit.json").write_text('{"pip_cves":[1,2,3],"npm_audits":[]}', encoding="utf-8")
    sig = scouts.security_signals(tmp_path)
    # rule_violations removed from scouts — now per-session DERIVED from telemetry
    assert "rule_violations" not in sig
    assert sig["canary_failures"] == 2
    assert sig["mechanism_orphans"] == 1
    assert sig["open_cves"] == 3


def test_remediation_efficacy(tmp_path):
    import json as _json
    from agentica_core import remediation
    hp = tmp_path / "h.jsonl"
    hp.write_text(
        _json.dumps({"ts": "2026-06-01T00:00:00+00:00", "values": {"bow/Autonomic/Mechanism_Orphans": 5.0}}) + "\n"
        + _json.dumps({"ts": "2026-06-01T02:00:00+00:00", "values": {"bow/Autonomic/Mechanism_Orphans": 1.0}}) + "\n",
        encoding="utf-8")
    recs = [{"timestamp": "2026-06-01T01:00:00+00:00", "skills_used": ["audit-mechanisms"]}]
    r = remediation.efficacy(history_path=hp, records=recs, exec_log_path=hp.parent / "empty_exec.jsonl")
    assert r["applied"] >= 1
    assert r["improved"] >= 1 and r["success_rate"] == 100.0
    # skill used but metric NOT flagged -> no event
    hp2 = tmp_path / "h2.jsonl"
    hp2.write_text(_json.dumps({"ts": "2026-06-01T00:00:00+00:00", "values": {"bow/Autonomic/Mechanism_Orphans": 0.0}}) + "\n"
                   + _json.dumps({"ts": "2026-06-01T02:00:00+00:00", "values": {"bow/Autonomic/Mechanism_Orphans": 0.0}}) + "\n", encoding="utf-8")
    assert remediation.efficacy(history_path=hp2, records=recs,
                                exec_log_path=hp.parent / "empty_exec.jsonl")["applied"] == 0


def test_insights_scores_flags_and_mitigation():
    from agentica_core import insights
    pillars = {
        "bow": {"Activity": {"Error_Rate": {"val": "9", "is_simulated": False}}},
        "sword": {"Vulnerability": {"Vulnerability_MTTR": {"val": "6", "is_simulated": False}}},
        "brush": {}, "arts": {},
    }
    scores = insights.annotate(pillars)
    # De-aggregation: no blended score — the breach shows in the status rollup
    assert "score" not in scores["sword"]
    assert scores["sword"]["rollup"]["worst"] in ("HIGH", "CRITICAL")
    assert any(f["name"] == "Vulnerability_MTTR" for f in scores["sword"]["flags"])
    # mitigation attached to the flagged metric
    assert pillars["sword"]["Vulnerability"]["Vulnerability_MTTR"]["mitigation_command"] == "/pip-safe-upgrade"
    sums = insights.build_summaries(pillars, scores)
    # summaries lead with the pillar's headline impact metric
    assert "This pillar tracked" in sums["sword"]
    assert "mean time to resolution" in sums["sword"]


def test_insights_history_and_trend(tmp_path):
    from agentica_core import insights
    store = tmp_path / "h.jsonl"
    insights.append_snapshot(store, "t1", {"bow/Activity/Complexity_Weighted_Throughput": 10.0})
    pillars = {"bow": {"Activity": {"Complexity_Weighted_Throughput": {"val": "14", "is_simulated": False}}}, "sword": {}, "brush": {}, "arts": {}}
    insights.populate_history(pillars, store=store)
    env = pillars["bow"]["Activity"]["Complexity_Weighted_Throughput"]
    assert env["history"] == [10.0, 14.0]
    assert env["trend"] == "up"


def test_score_architecture_weighted(tmp_path):
    import json as _json
    from agentica_core import scouts
    sc = tmp_path / "sc.json"
    sc.write_text(_json.dumps({"categories": [
        {"id": "path_authority", "weight": 40}, {"id": "root_hygiene", "weight": 60}]}), encoding="utf-8")
    # a FAIL mapped to root_hygiene loses its 60; path_authority keeps 40
    res = [{"status": "OK", "label": "path-authority-scan"}, {"status": "FAIL", "label": "root_hygiene.unclassified"}]
    assert scouts.score_architecture(res, sc) == 40.0
    assert scouts.score_architecture([{"status": "OK", "label": "x"}], sc) == 100.0


def test_security_signals_injected_real():
    from datetime import datetime, timezone
    # Rule_Violations now comes from per-session telemetry via REGISTRY, not scouts.
    # Verify it appears as SIMULATED (no records) when no telemetry present.
    p = agg.build_pillars([], security_signals={"gate_fires": 7})
    rv = p["sword"]["Governance"]["Rule_Violations"]
    assert rv["is_simulated"] is True  # no records → SIMULATED (correct)
    # Verify it becomes DERIVED when telemetry records carry rule_violations this week.
    # Must use current-week timestamp — Rule_Violations switched to r_sum_field_weekly.
    now_ts = datetime.now(timezone.utc).isoformat()
    p2 = agg.build_pillars([{"rule_violations": 5, "status": "success", "timestamp": now_ts,
                              "platform": "claude", "latency_ms": 100}])
    rv2 = p2["sword"]["Governance"]["Rule_Violations"]
    assert rv2["val"] == "5" and rv2["is_simulated"] is False and rv2["tier"] == "DERIVED"


def test_arts_transcript_reducers_real():
    from datetime import datetime, timezone
    # Frustration_Signals and Rework_Loops switched to r_sum_field_weekly — records
    # must carry a current-week timestamp to register as live (not None/SIMULATED).
    now_ts = datetime.now(timezone.utc).isoformat()
    recs = [
        {"timestamp": now_ts, "slop_markers": 3, "output_words": 1000, "frustration_signals": 1, "rework_turns": 2, "skills_used": ["simplify"]},
        {"timestamp": now_ts, "slop_markers": 1, "output_words": 1000, "frustration_signals": 0, "rework_turns": 1, "skills_used": []},
    ]
    p = agg.build_pillars(recs)
    assert p["arts"]["Output Quality"]["Slop_Density"]["val"] == "2.0"      # 4 / 2000 * 1000
    assert p["arts"]["Output Quality"]["Slop_Density"]["is_simulated"] is False
    assert p["arts"]["Interaction"]["Frustration_Signals"]["val"] == "1"
    assert p["arts"]["Interaction"]["Rework_Loops"]["val"] == "3"
    assert p["arts"]["Process"]["Simplify_Runs"]["val"] == "1"


def test_arts_simulated_without_transcript_fields():
    p = agg.build_pillars([{"status": "success", "tokens_prompt": 1}])
    assert p["arts"]["Output Quality"]["Slop_Density"]["is_simulated"] is True
    assert p["arts"]["Process"]["Simplify_Runs"]["is_simulated"] is True


def test_cost_per_task_excludes_records_without_cost():
    """r_cost_per_task must divide by the count of records that HAVE cost data,
    not the total record count — otherwise it systematically understates cost."""
    recs_partial = [
        {"total_cost": 0.10},  # has cost
        {"total_cost": 0.20},  # has cost
        {},                    # no total_cost field — must NOT inflate denominator
        {"status": "success"}, # no total_cost field — must NOT inflate denominator
    ]
    result = agg.r_cost_per_task(recs_partial)
    # With correct denominator (len=2): 0.30/2 = 0.15
    # With wrong denominator (len=4):  0.30/4 = 0.075  ← the old bug
    assert result == 0.15, f"expected 0.15, got {result}"


def test_derive_verifier_metrics_mapping():
    results = [
        {"status": "OK", "label": "path-authority-scan", "detail": ""},
        {"status": "FAIL", "label": "archive-boundary-scan", "detail": ""},
        {"status": "WARN", "label": "root_hygiene.unclassified", "detail": ""},
    ]
    vm = agg.derive_verifier_metrics(results)
    assert vm["Boundary_Violations"] == 1
    assert vm["Root_Hygiene_Issues"] == 1
    assert vm["Governance_Pass_Rate"] == round(100 / 3, 1)


def test_verifier_failure_remediation_targets_failing_platform():
    p = agg.build_pillars([], verifier_results=[
        {"status": "FAIL", "label": "hub_surface_matrix.json", "detail": "", "platform": "antigravity"},
        {"status": "OK", "label": "codex-telemetry-schema", "detail": "", "platform": "codex"},
    ])
    vf = p["bow"]["Governance"]["Verifier_Failures"]
    assert vf["val"] == "1"
    assert vf["failure_platforms"] == ["antigravity"]
    assert vf["mitigation_command"] == "python -m agentica_core.doctor antigravity"


def test_vault_health_metrics_live_in_arts():
    """Knowledge vault health flows into arts/Knowledge as live AUTO metrics WHEN a vault is
    present. The vault is an optional external integration (AGENTICA_OS_ROOT); on a fresh
    install it is absent and the metric is honestly SIMULATED, so this test skips rather than
    coupling the suite to a machine with a vault."""
    import pytest
    payload = agg.aggregate()
    knowledge = payload["pillars"]["arts"].get("Knowledge", {})
    score_env = knowledge.get("Wiki_Health_Score")
    if not score_env or score_env.get("is_simulated", True):
        pytest.skip("knowledge vault not present in this environment (optional integration)")
    assert "Wiki_Article_Count" in knowledge
    assert score_env["tier"] == "AUTO"
    assert score_env["is_simulated"] is False
    assert float(score_env["val"]) > 0, "vault health score should be > 0"


def test_vault_health_metrics_contract_valid():
    """Every vault metric envelope satisfies the telemetry contract."""
    from agentica_core.telemetry import validate_metric
    payload = agg.aggregate()
    knowledge = payload["pillars"]["arts"].get("Knowledge", {})
    for name, env in knowledge.items():
        validate_metric(env)  # must not raise


def test_weekly_sum_counts_only_current_iso_week():
    """r_sum_field_weekly windows to the current ISO week; lifetime twin counts all."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    this_week_ts = now.isoformat()
    last_week_ts = (now - timedelta(days=8)).isoformat()
    recs = [
        {"timestamp": this_week_ts, "rule_violations": 2},
        {"timestamp": this_week_ts, "rule_violations": 3},
        {"timestamp": last_week_ts, "rule_violations": 100},
        {"rule_violations": 50},  # no timestamp -> excluded from weekly, counted in lifetime
    ]
    weekly = agg.r_sum_field_weekly("rule_violations")(recs)
    lifetime = agg.r_sum_field("rule_violations")(recs)
    assert weekly == 5, f"weekly should count only current-week records, got {weekly}"
    assert lifetime == 155


def test_weekly_sum_clean_week_is_zero_not_none():
    """Records exist this week but carry no violations -> honest 0, not no-signal."""
    from datetime import datetime, timezone
    recs = [{"timestamp": datetime.now(timezone.utc).isoformat(), "rule_violations": 0}]
    assert agg.r_sum_field_weekly("rule_violations")(recs) == 0


def test_weekly_sum_no_records_this_week_is_none():
    """No records at all in the current week -> None (no signal), never a fake 0."""
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert agg.r_sum_field_weekly("rule_violations")([{"timestamp": old, "rule_violations": 9}]) is None


def test_load_records_skips_synthetic_model(tmp_path, monkeypatch):
    """Records with model '<synthetic>' (fabricated by ad-hoc backfill) are rejected
    at the read funnel so they never pollute cost/token metrics."""
    src = tmp_path / "telemetry.jsonl"
    base = {"schema_version": "agentica.1", "timestamp": "2026-06-13T00:00:00+00:00",
            "platform": "claude", "project": "p", "task_name": "session",
            "model_tier": "PREMIUM", "latency_ms": 0.0, "status": "success"}
    real = {**base, "model": "claude-opus-4-8", "tokens_prompt": 100,
            "tokens_completion": 50, "total_cost": 0.5, "session_id": "real1"}
    fake = {**base, "model": "<synthetic>", "tokens_prompt": 999999,
            "tokens_completion": 999999, "total_cost": 242.78, "session_id": "fake1"}
    src.write_text("\n".join(json.dumps(r) for r in (real, fake)) + "\n", encoding="utf-8")

    class _FakePlatform:
        telemetry_source = src
    monkeypatch.setattr(agg, "resolve_platform", lambda platform: _FakePlatform())

    out = agg.load_records("claude")
    models = [r.get("model") for r in out]
    assert "<synthetic>" not in models
    assert "claude-opus-4-8" in models


def test_coef_block_calibrated_requires_real_samples():
    """A coefficient marked calibrated=true with 0 samples is NOT calibrated —
    the stored flag is not trusted (seeded files lie), sample_count gates it."""
    seeded_lie = {"skill": {"benchmark_min": 30, "calibrated": True, "sample_count": 0}}
    assert agg._coef_block_calibrated(seeded_lie) is False

    real = {"skill": {"benchmark_min": 30, "calibrated": True, "sample_count": 20}}
    assert agg._coef_block_calibrated(real) is True

    # any single under-sampled entry fails the whole block
    mixed = {
        "a": {"calibrated": True, "sample_count": 25},
        "b": {"calibrated": True, "sample_count": 3},
    }
    assert agg._coef_block_calibrated(mixed) is False
    assert agg._coef_block_calibrated({}) is False


def test_coef_block_calibrated_honors_time_marker():
    """A time-bounded calibration (real but fewer than min_samples) is trusted when
    tagged calibrated_via='time' — the 4-week fallback path. Untagged under-sampled
    entries, and the zero-sample seed, are still rejected."""
    time_calibrated = {"skill": {"calibrated": True, "sample_count": 3, "calibrated_via": "time"}}
    assert agg._coef_block_calibrated(time_calibrated, min_samples=10) is True

    untagged = {"skill": {"calibrated": True, "sample_count": 3}}
    assert agg._coef_block_calibrated(untagged, min_samples=10) is False

    seed = {"skill": {"calibrated": True, "sample_count": 0}}
    assert agg._coef_block_calibrated(seed, min_samples=10) is False


def _build_coef(tmp_path, *, weeks=4, samples=10):
    coef = {
        "operations": {
            "stream": {"benchmark_min": 45, "calibrated": False, "sample_count": 0},
            "field": {"benchmark_min": 90, "calibrated": False, "sample_count": 0},
        },
        "calibration_threshold": {"samples": samples, "weeks": weeks},
    }
    p = tmp_path / "calibration_coefficients.json"
    p.write_text(json.dumps(coef), encoding="utf-8")
    return p


def _done_item(kind, started, completed):
    return {"status": "done", "kind": kind, "started_at": started, "completed_at": completed}


def test_calibrate_coefficients_time_bounded_fallback_after_weeks(tmp_path):
    """Fewer than `samples` timed samples but >= `weeks` of collection calibrates from
    whatever real samples exist and tags them calibrated_via='time'."""
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(weeks=5)
    backlog = [
        _done_item("stream", old.isoformat(), (old + timedelta(minutes=30)).isoformat()),
        _done_item("field", old.isoformat(), (old + timedelta(minutes=60)).isoformat()),
    ]
    coef_path = _build_coef(tmp_path, weeks=4, samples=10)

    agg._calibrate_coefficients(backlog, coef_path)

    coef = json.loads(coef_path.read_text(encoding="utf-8"))
    assert coef["operations"]["stream"]["calibrated"] is True
    assert coef["operations"]["stream"]["calibrated_via"] == "time"
    assert coef["operations"]["stream"]["sample_count"] == 1
    assert coef["operations"]["stream"]["benchmark_min"] == 30
    assert agg._coef_block_calibrated(coef["operations"], min_samples=10) is True


def test_calibrate_coefficients_no_fallback_when_recent(tmp_path):
    """Fewer than `samples` timed samples AND collection younger than `weeks` must not
    calibrate — the time fallback must not fire early."""
    from datetime import datetime, timedelta, timezone
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    backlog = [
        _done_item("stream", recent.isoformat(), (recent + timedelta(minutes=30)).isoformat()),
    ]
    coef_path = _build_coef(tmp_path, weeks=4, samples=10)

    agg._calibrate_coefficients(backlog, coef_path)

    coef = json.loads(coef_path.read_text(encoding="utf-8"))
    assert coef["operations"]["stream"]["calibrated"] is False
    assert "calibrated_via" not in coef["operations"]["stream"]


def _agent_repo(tmp_path, backlog, ops):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "DOJO_STATE.json").write_text(json.dumps({"backlog": backlog}), encoding="utf-8")
    (state_dir / "calibration_coefficients.json").write_text(
        json.dumps({"operations": ops, "calibration_threshold": {"samples": 10, "weeks": 4}}),
        encoding="utf-8")
    return tmp_path


def test_agent_time_saved_calibrates_on_contributing_kinds_only(tmp_path):
    """A stream-only week is calibrated when stream is calibrated — it must not wait
    on a skill benchmark that no work-unit ever samples."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    repo = _agent_repo(
        tmp_path,
        backlog=[{"id": "x", "kind": "stream", "status": "done",
                  "started_at": now_iso, "completed_at": now_iso}],
        ops={"stream": {"benchmark_min": 40, "calibrated": True, "sample_count": 10},
             "skill": {"benchmark_min": 30, "calibrated": False, "sample_count": 0}},
    )
    assert agg._estimated_agent_time_saved([], repo_root=repo)["calibrated"] is True


def test_agent_time_saved_uncalibrated_when_a_contributing_kind_is_not(tmp_path):
    """If a kind contributing this week is uncalibrated, Bow is uncalibrated."""
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    repo = _agent_repo(
        tmp_path,
        backlog=[{"id": "x", "kind": "scout", "status": "done",
                  "started_at": now_iso, "completed_at": now_iso}],
        ops={"scout": {"benchmark_min": 20, "calibrated": False, "sample_count": 0}},
    )
    assert agg._estimated_agent_time_saved([], repo_root=repo)["calibrated"] is False


def test_agent_time_saved_uncalibrated_when_no_items_this_week(tmp_path):
    """No done items this week = nothing measured = uncalibrated (hero falls back)."""
    repo = _agent_repo(
        tmp_path,
        backlog=[{"id": "old", "kind": "stream", "status": "done",
                  "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:30:00Z"}],
        ops={"stream": {"benchmark_min": 40, "calibrated": True, "sample_count": 10}},
    )
    assert agg._estimated_agent_time_saved([], repo_root=repo)["calibrated"] is False

def test_kill_chains_disrupted_data_gap_when_no_source(monkeypatch):
    """No kill_chain_events source anywhere = dead/unwired emitter -> data_gap True,
    so the Sword hero falls back to Security_Scorecard instead of showing a confident
    (possibly false) '0 disrupted'."""
    from pathlib import Path
    monkeypatch.setattr(agg, "_kill_chain_paths", lambda rr: [Path("Z:/nonexistent/kc.jsonl")])
    r = agg._kill_chains_disrupted([])
    assert r["val"] == 0 and r.get("data_gap") is True
