"""Tests for reconcile_state.py, the deterministic daily reconciler
meditation_overnight.sh calls in place of the retired self-harness loop.

First test coverage for this module — it shipped untracked/uncommitted
(P1 #4 from a 10-agent code-review test of compound-engineering:ce-code-review),
landed alongside meditation_overnight.sh's exit-code-capture fix (P1 #5).
Kept intentionally modest: the module's own subprocess calls (real verifier
scripts) are exercised with fake commands here so these tests stay fast and
hermetic, not a re-test of those scripts' own logic (they have their own
suites).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reconcile_state as rs  # noqa: E402


def test_stage_1_removes_only_files_older_than_a_day(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(rs, "TMP_DIR", tmp_path)
    old = tmp_path / "old.log"
    old.write_text("stale")
    fresh = tmp_path / "fresh.log"
    fresh.write_text("new")
    old_time = time.time() - 90000  # > 86400s
    import os
    os.utime(old, (old_time, old_time))

    result = rs.stage_1_telemetry_compaction()

    assert result["status"] == "PASS"
    assert result["cleaned_temp_files"] == 1
    assert not old.exists()
    assert fresh.exists()


def test_stage_1_survives_a_missing_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "TMP_DIR", tmp_path / "does_not_exist")
    result = rs.stage_1_telemetry_compaction()
    assert result["status"] == "PASS"
    assert result["cleaned_temp_files"] == 0


def test_stage_2_reports_pass_when_every_script_exits_zero(tmp_path):
    """stage_2_deterministic_sweep's script list is a module-level literal, not
    injectable -- exercised here via a real repo_root laid out with the exact
    relative script paths it expects, each a trivial always-pass stub, so this
    tests the REAL function's aggregation logic without running the actual
    (slow, heavier) verifier scripts."""
    (tmp_path / "execution").mkdir()
    (tmp_path / "bin").mkdir()
    stub = "import sys; print('ok'); sys.exit(0)\n"
    for rel in ("execution/verify_path_authority.py", "execution/verify_root_hygiene.py",
                "execution/timeout_audit_scan.py", "execution/verify_doc_parity.py",
                "bin/mcp_smoke_test.py", "bin/secret_scrub.py"):
        (tmp_path / rel).write_text(stub)

    result = rs.stage_2_deterministic_sweep(tmp_path)

    assert result["status"] == "PASS"
    assert result["passed"] == result["total_checks"]
    assert result["failed"] == 0


def test_stage_2_reports_warn_not_fail_when_one_script_exits_nonzero(tmp_path):
    """A single failing scanner degrades the sweep to WARN, not a hard FAIL --
    matches run_reconciliation's own PASS-or-WARN-is-acceptable contract."""
    (tmp_path / "execution").mkdir()
    (tmp_path / "bin").mkdir()
    ok_stub = "import sys; sys.exit(0)\n"
    for rel in ("execution/verify_path_authority.py", "execution/timeout_audit_scan.py",
                "execution/verify_doc_parity.py", "bin/mcp_smoke_test.py", "bin/secret_scrub.py"):
        (tmp_path / rel).write_text(ok_stub)
    (tmp_path / "execution/verify_root_hygiene.py").write_text("import sys; sys.exit(2)\n")

    result = rs.stage_2_deterministic_sweep(tmp_path)

    assert result["status"] == "WARN"
    assert result["failed"] == 1
    assert result["details"]["root_hygiene"]["status"] == "FAIL"
    assert result["details"]["root_hygiene"]["exit_code"] == 2


def test_stage_3_falsifiability_reports_pass_on_zero_exit(tmp_path):
    ok_script = tmp_path / "execution"
    ok_script.mkdir()
    (ok_script / "verify_falsifiability.py").write_text("import sys; sys.exit(0)\n")
    result = rs.stage_3_falsifiability(tmp_path)
    assert result["status"] == "PASS"
    assert result["exit_code"] == 0


def test_stage_3_falsifiability_reports_warn_not_crash_on_nonzero_exit(tmp_path):
    ok_script = tmp_path / "execution"
    ok_script.mkdir()
    (ok_script / "verify_falsifiability.py").write_text("import sys; sys.exit(1)\n")
    result = rs.stage_3_falsifiability(tmp_path)
    assert result["status"] == "WARN"
    assert result["exit_code"] == 1


def test_stage_4_writes_a_report_with_the_real_stage_results(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "STATE_DIR", tmp_path / "state")
    stage_results = [
        {"stage": "telemetry_compaction", "status": "PASS", "duration_s": 0.1},
        {"stage": "deterministic_sweep", "status": "WARN", "duration_s": 1.2},
    ]
    result = rs.stage_4_morning_briefing(stage_results, tmp_path)
    assert result["status"] == "PASS"
    report_path = Path(result["report_path"])
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["overall_status"] == "PASS"   # WARN still counts as overall PASS
    assert "telemetry_compaction" in report["stages"]
    assert report["cost_usd"] == 0.0


def test_stage_4_overall_status_is_fail_when_any_stage_hard_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "STATE_DIR", tmp_path / "state")
    stage_results = [
        {"stage": "telemetry_compaction", "status": "PASS", "duration_s": 0.1},
        {"stage": "deterministic_sweep", "status": "FAIL", "duration_s": 1.2},
    ]
    result = rs.stage_4_morning_briefing(stage_results, tmp_path)
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["overall_status"] == "FAIL"


def test_main_returns_zero_exit_when_overall_status_is_pass(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["reconcile_state.py"])
    monkeypatch.setattr(rs, "run_reconciliation",
                        lambda repo_root=rs.REPO_ROOT: {
                            "status": "PASS", "total_duration_s": 0.5, "cost_usd": 0.0,
                            "stages": [{"stage": "x", "status": "PASS", "duration_s": 0.1}],
                        })
    assert rs.main() == 0


def test_main_returns_nonzero_exit_when_overall_status_is_fail(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["reconcile_state.py"])
    monkeypatch.setattr(rs, "run_reconciliation",
                        lambda repo_root=rs.REPO_ROOT: {
                            "status": "FAIL", "total_duration_s": 0.5, "cost_usd": 0.0,
                            "stages": [{"stage": "x", "status": "FAIL", "duration_s": 0.1}],
                        })
    assert rs.main() == 1


def test_main_json_flag_prints_valid_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["reconcile_state.py", "--json"])
    monkeypatch.setattr(rs, "run_reconciliation",
                        lambda repo_root=rs.REPO_ROOT: {
                            "status": "PASS", "total_duration_s": 0.5, "cost_usd": 0.0,
                            "stages": [{"stage": "x", "status": "PASS", "duration_s": 0.1}],
                        })
    rs.main()
    out = capsys.readouterr().out
    parsed = json.loads(out)   # must not raise -- this is the machine-readable contract
    assert parsed["status"] == "PASS"


def test_run_reconciliation_never_raises_even_if_a_stage_script_is_missing(tmp_path):
    """End-to-end smoke test against a repo_root with none of the real
    verifier scripts present -- every stage-2/3 subprocess call fails to
    launch, and run_reconciliation must still return a structured result,
    never propagate an exception (this is what meditation_overnight.sh's
    fix now depends on to get a real, reportable exit code)."""
    result = rs.run_reconciliation(repo_root=tmp_path)
    assert result["status"] in ("PASS", "WARN", "FAIL")
    assert isinstance(result["stages"], list)
    assert len(result["stages"]) == 4
