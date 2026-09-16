from __future__ import annotations

import json
import os
import plistlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from execution.scheduled_run_outcomes import run_checks


NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
JOB = "com.agentica.test-job"


def _write_plist(
    path: Path, *, hour: int | tuple[int, ...] = 2, run_at_load: bool = False,
    stdout: Path | None = None,
) -> None:
    """A tuple of hours writes launchd's list form — the shape a fast-cadence calendar job
    has to use, since one StartCalendarInterval dict can only name one hour."""
    entries = [{"Hour": value, "Minute": 0} for value in
               ((hour,) if isinstance(hour, int) else hour)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps({
        "Label": path.stem,
        "ProgramArguments": ["/bin/echo", "ok"],
        "StartCalendarInterval": entries[0] if len(entries) == 1 else entries,
        "RunAtLoad": run_at_load,
        "StandardOutPath": str(stdout or path.parent / f"{path.stem}.log"),
    }))


def _touch(path: Path, age_hours: float, text: str = "data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stamp = (NOW - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (stamp, stamp))


def _proc(status: int = 0, job_exit: int = 0) -> subprocess.CompletedProcess:
    stdout = f"-\t{job_exit}\t{JOB}\n" if status == 0 else ""
    return subprocess.CompletedProcess([], status, stdout=stdout, stderr="")


def _setup(tmp_path: Path, **job_changes) -> dict:
    repo = Path(__file__).resolve().parents[3]
    source = tmp_path / "source"
    installed = tmp_path / "installed"
    artifact = tmp_path / "artifact.json"
    log = tmp_path / "job.log"
    plist_log = tmp_path / "plist.log"
    _write_plist(source / f"{JOB}.plist", stdout=plist_log)
    _write_plist(installed / f"{JOB}.plist", stdout=plist_log)
    _touch(artifact, 1)
    _touch(log, 1, "2026-09-02T02:00:00+00:00 run\n")
    job = {
        "id": JOB,
        "cadence_hours": 24,
        "artifact": str(artifact),
        "may_be_empty": False,
        "log": str(log),
    }
    job.update(job_changes)
    registry = tmp_path / "operator_registry.json"
    registry.write_text(json.dumps({"automations": [job]}), encoding="utf-8")
    scheduler = tmp_path / "scheduled_tasks.json"
    scheduler.write_text(json.dumps({"tasks": [{
        "launchd_id": JOB,
        "plist_file": f"{JOB}.plist",
    }]}), encoding="utf-8")
    zone_target = tmp_path / "zoneinfo" / "Test" / "UTC"
    zone_target.parent.mkdir(parents=True)
    zone_target.touch()
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zone_target)
    return {
        "repo_root": repo,
        "registry_path": registry,
        "source_dir": source,
        "installed_dir": installed,
        "scheduled_tasks_paths": (scheduler,),
        "launchctl_runner": _proc,
        "now": NOW,
        "localtime_path": localtime,
        "process_offset": timedelta(0),
        "zoneinfo": lambda _name: timezone.utc,
        # The plist hours in these fixtures are written in UTC; pin the comparison zone
        # so the suite reads the same on a New York host as on CI.
        "local_zone": timezone.utc,
        "artifact": artifact,
        "log": log,
    }


def _repoint_schedule(config: dict, hours: tuple[int, ...]) -> None:
    """Move both the source and the installed plist onto a multi-hour calendar schedule,
    keeping them identical so the change reads as a schedule, not as plist drift."""
    for directory in (config["source_dir"], config["installed_dir"]):
        _write_plist(directory / f"{JOB}.plist", hour=hours,
                     stdout=config["log"].parent / "plist.log")


def _run(config: dict) -> list[dict]:
    private = {"artifact", "log"}
    return run_checks(**{key: value for key, value in config.items() if key not in private})


def _labels(rows: list[dict]) -> list[str]:
    return [row["label"] for row in rows]


def test_fresh_artifact_and_matching_schedule_are_healthy(tmp_path: Path):
    rows = _run(_setup(tmp_path))
    assert rows == [{
        "status": "OK",
        "label": "scheduled-run-outcomes.healthy",
        "detail": "1 job(s) measured, all outcomes on contract",
    }]


def test_stale_artifact_fails(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["artifact"], 49)
    _touch(config["log"], 25)
    rows = _run(config)
    assert "scheduled-run-outcomes.stale-artifact" in _labels(rows)


def test_fresh_log_and_stale_artifact_is_ran_empty(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["artifact"], 49)
    rows = _run(config)
    assert "scheduled-run-outcomes.ran-empty" in _labels(rows)
    assert "scheduled-run-outcomes.stale-artifact" not in _labels(rows)


def test_may_be_empty_suppresses_only_ran_empty(tmp_path: Path):
    config = _setup(tmp_path, may_be_empty=True)
    _touch(config["artifact"], 49)
    labels = _labels(_run(config))
    assert "scheduled-run-outcomes.stale-artifact" in labels
    assert "scheduled-run-outcomes.ran-empty" not in labels


def test_nonzero_launchctl_status_and_old_log_warns_never_started(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["log"], 25)
    config["launchctl_runner"] = lambda: _proc(job_exit=78)
    assert "scheduled-run-outcomes.never-started" in _labels(_run(config))


def test_installed_job_absent_from_successful_launchctl_list_warns(tmp_path: Path):
    config = _setup(tmp_path)
    config["launchctl_runner"] = lambda: subprocess.CompletedProcess([], 0, stdout="", stderr="")
    rows = _run(config)
    assert any(row["label"].endswith(".never-started") and "not loaded" in row["detail"]
               for row in rows)


def test_launchctl_failure_is_unmeasured_not_an_empty_job_set(tmp_path: Path):
    config = _setup(tmp_path)
    config["launchctl_runner"] = lambda: _proc(status=1)
    rows = _run(config)
    assert any(row["label"].endswith(".unmeasured") and "launchctl" in row["detail"] for row in rows)


def test_missing_artifact_contract_is_unmeasured_with_reason(tmp_path: Path):
    config = _setup(tmp_path, artifact=None, artifact_reason="side-effect only")
    rows = _run(config)
    assert any(row["label"].endswith(".unmeasured") and "side-effect only" in row["detail"] for row in rows)


def test_registry_without_outcome_contracts_is_unmeasured_not_healthy(tmp_path: Path):
    """The pre-revamp registry shape (no cadence_hours anywhere) measures nothing and must
    say so — the shared checkout runs exactly that shape until it is resynced."""
    config = _setup(tmp_path)
    config["registry_path"].write_text(json.dumps({"automations": [
        {"id": JOB, "type": "time", "cadence": "daily", "owner": "x", "cost_cap_usd": 0}]}),
        encoding="utf-8")
    rows = _run(config)
    assert not any(row["status"] == "OK" for row in rows)
    assert any(row["label"] == "scheduled-run-outcomes.unmeasured"
               and "0 outcome contracts" in row["detail"] for row in rows)


def test_malformed_outcome_contract_is_explicitly_unmeasured(tmp_path: Path):
    config = _setup(tmp_path, cadence_hours="daily", may_be_empty="no")
    rows = _run(config)
    assert any("invalid outcome contract" in row["detail"]
               and "cadence_hours" in row["detail"] for row in rows)


def test_installed_unregistered_job_warns_undeclared(tmp_path: Path):
    config = _setup(tmp_path)
    _write_plist(config["installed_dir"] / "com.agentica.extra.plist")
    assert "scheduled-run-outcomes.undeclared" in _labels(_run(config))


def test_first_fire_hour_drift_fails(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["log"], 1, "2026-09-02T04:00:00+00:00 run\n")
    assert "scheduled-run-outcomes.tz-drift" in _labels(_run(config))


def test_first_fire_uses_the_start_of_the_last_run_not_its_last_line(tmp_path: Path):
    """A nightly job that fires at 02:00 and logs until 05:40 is on time, not 3h late."""
    config = _setup(tmp_path)
    _touch(config["log"], 1,
           "2026-09-01T02:00:03+00:00 started\n2026-09-01T04:10:00+00:00 working\n"
           "2026-09-01T05:40:00+00:00 done\n"
           "2026-09-02T02:00:02+00:00 started\n2026-09-02T05:41:00+00:00 done\n")
    assert "scheduled-run-outcomes.tz-drift" not in _labels(_run(config))


def test_first_fire_compares_hours_in_the_launchd_zone(tmp_path: Path):
    """A `Z`-stamped log and a local-hour plist must be compared in one zone: 06:00Z is
    02:00 in UTC-4, which is the planned hour, so no drift; read as UTC it would be 4h off."""
    config = _setup(tmp_path)
    _touch(config["log"], 1, "2026-09-02T06:00:00+00:00 run\n")
    config["local_zone"] = timezone(timedelta(hours=-4))
    assert "scheduled-run-outcomes.tz-drift" not in _labels(_run(config))
    config["local_zone"] = timezone.utc
    assert "scheduled-run-outcomes.tz-drift" in _labels(_run(config))


def test_missing_first_fire_timestamp_is_unmeasured(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["log"], 1, "run started, timestamp unknown\n")
    rows = _run(config)
    assert any(row["label"].endswith(".unmeasured") and "first-fire" in row["detail"] for row in rows)


def test_old_timestamp_in_finding_prose_is_not_a_fire_time(tmp_path: Path):
    config = _setup(tmp_path)
    _touch(config["log"], 1,
           "[WARN] newest incident at 2026-08-29T08:45:40+00:00\n")
    rows = _run(config)
    assert "scheduled-run-outcomes.tz-drift" not in _labels(rows)
    assert any("first-fire timestamp" in row["detail"] for row in rows)


def test_old_anchored_timestamp_is_unmeasured_not_tz_drift(tmp_path: Path):
    """08:45 against a planned 02:00 would read as 6h of drift; the stamp is 99h old, so it
    is a stale sample rather than evidence about the fire hour. The reported age and limit
    are asserted so the row cannot silently change which threshold it enforces."""
    config = _setup(tmp_path)
    _touch(config["log"], 1, "2026-08-29T08:45:40+00:00 old run\n")
    rows = _run(config)
    assert "scheduled-run-outcomes.tz-drift" not in _labels(rows)
    assert [row["detail"] for row in rows if "anchored log stamp" in row["detail"]] == [
        f"{JOB}: newest anchored log stamp is 99.2h old; limit 48h"
    ]


def test_calendar_job_sampled_after_its_next_fire_hour_is_still_graded(tmp_path: Path):
    """The 12:00 run of a 6h job has not written its first line yet when the doctor samples
    at 12:30, so the newest anchored stamp is the 06:00 fire — 6.5h old, on its planned
    hour, and healthy. A bare cadence bound WARNs on every such sample."""
    config = _setup(tmp_path, cadence_hours=6)
    _repoint_schedule(config, (0, 6, 12, 18))
    _touch(config["log"], 1, "2026-09-02T06:00:00+00:00 run\n")
    config["now"] = NOW + timedelta(minutes=30)
    assert _run(config) == [{
        "status": "OK",
        "label": "scheduled-run-outcomes.healthy",
        "detail": "1 job(s) measured, all outcomes on contract",
    }]


def test_short_cadence_job_that_skipped_a_fire_is_still_graded(tmp_path: Path):
    """A 2h job whose newest stamp is 4.5h old has missed a fire, but its hour is still
    evidence about timezone alignment. The 6h floor keeps short cadences gradable — twice
    the cadence alone (4h) would drop this sample."""
    config = _setup(tmp_path, cadence_hours=2)
    _repoint_schedule(config, (8, 10, 12))
    _touch(config["log"], 1, "2026-09-02T08:00:00+00:00 run\n")
    config["now"] = NOW + timedelta(minutes=30)
    assert _labels(_run(config)) == ["scheduled-run-outcomes.healthy"]


def test_calendar_job_past_two_cadences_is_unmeasured(tmp_path: Path):
    """The gate still fires: 18.5h is past two cadences of a 6h job, so the sample is no
    longer trusted to describe the current fire hour even though 18:00 is a planned hour."""
    config = _setup(tmp_path, cadence_hours=6)
    _repoint_schedule(config, (0, 6, 12, 18))
    _touch(config["log"], 1, "2026-09-01T18:00:00+00:00 run\n")
    config["now"] = NOW + timedelta(minutes=30)
    rows = _run(config)
    assert [row["detail"] for row in rows if "anchored log stamp" in row["detail"]] == [
        f"{JOB}: newest anchored log stamp is 18.5h old; limit 12h"
    ]


def test_stamp_exactly_at_the_limit_is_still_graded(tmp_path: Path):
    """The upper bound is inclusive: at exactly two cadences the sample is the oldest one
    still worth grading, so a planned hour reads healthy rather than unmeasured."""
    config = _setup(tmp_path, cadence_hours=6)
    _repoint_schedule(config, (0, 6, 12, 18))
    _touch(config["log"], 1, "2026-09-02T00:30:00+00:00 run\n")
    config["now"] = NOW + timedelta(minutes=30)   # exactly 12.0h after the stamp
    assert _labels(_run(config)) == ["scheduled-run-outcomes.healthy"]


def test_stamp_just_past_the_limit_is_unmeasured(tmp_path: Path):
    """Six minutes past the same bound flips it: exclusive above the limit."""
    config = _setup(tmp_path, cadence_hours=6)
    _repoint_schedule(config, (0, 6, 12, 18))
    _touch(config["log"], 1, "2026-09-02T00:24:00+00:00 run\n")
    config["now"] = NOW + timedelta(minutes=30)   # 12.1h after the stamp
    rows = _run(config)
    assert [row["detail"] for row in rows if "anchored log stamp" in row["detail"]] == [
        f"{JOB}: newest anchored log stamp is 12.1h old; limit 12h"
    ]


def test_future_anchored_timestamp_is_unmeasured_not_tz_drift(tmp_path: Path):
    """A stamp ahead of the clock is a wrong clock or a mis-parsed zone, not a run. 14:00
    against a planned 02:00 would otherwise be graded as 12h of drift — a FAIL invented
    from an input the module has already been told not to trust."""
    config = _setup(tmp_path)
    _touch(config["log"], 1, "2026-09-02T14:00:00+00:00 run\n")
    rows = _run(config)
    assert "scheduled-run-outcomes.tz-drift" not in _labels(rows)
    assert [row["detail"] for row in rows if "anchored log stamp" in row["detail"]] == [
        f"{JOB}: newest anchored log stamp is 2.0h in the future; skew tolerance 5min"
    ]


def test_stamp_inside_the_skew_tolerance_is_still_graded(tmp_path: Path):
    """Two minutes ahead is ordinary clock skew between the logging process and this one —
    the job fired on its planned hour and must still read healthy."""
    config = _setup(tmp_path)
    _touch(config["log"], 1, "2026-09-02T02:00:00+00:00 run\n")
    config["now"] = datetime(2026, 9, 2, 1, 58, tzinfo=timezone.utc)
    assert _labels(_run(config)) == ["scheduled-run-outcomes.healthy"]


def test_plist_diff_covers_values_including_run_at_load_and_uninstalled_sources(tmp_path: Path):
    """Forward direction + value diff live here; the reverse direction (installed with no
    source) is doctor's factory.plist-drift, which honours the acknowledged-unsourced
    policy."""
    config = _setup(tmp_path)
    _write_plist(
        config["installed_dir"] / f"{JOB}.plist", run_at_load=True,
        stdout=config["source_dir"].parent / "plist.log",
    )
    _write_plist(config["source_dir"] / "com.agentica.source-only.plist")
    _write_plist(config["installed_dir"] / "com.agentica.live-only.plist")
    rows = _run(config)
    details = "\n".join(row["detail"] for row in rows if row["label"].endswith(".plist-drift"))
    assert "RunAtLoad" in details
    assert "source-only" in details
    assert "live-only" not in details


def test_timing_is_graded_against_installed_not_drifted_source_plist(tmp_path: Path):
    config = _setup(tmp_path)
    _write_plist(config["installed_dir"] / f"{JOB}.plist", hour=4)
    _touch(config["log"], 1, "2026-09-02T04:00:00+00:00 run\n")
    rows = _run(config)
    assert "scheduled-run-outcomes.plist-drift" in _labels(rows)
    assert "scheduled-run-outcomes.tz-drift" not in _labels(rows)


def test_relative_runtime_artifact_uses_main_checkout(tmp_path: Path):
    config = _setup(tmp_path, artifact="Data/result.json")
    runtime = tmp_path / "main-checkout"
    _touch(runtime / "Data" / "result.json", 1)
    config["runtime_root"] = runtime
    assert "scheduled-run-outcomes.stale-artifact" not in _labels(_run(config))


def test_strict_plist_probe_warns_on_xml_comments(tmp_path: Path):
    config = _setup(tmp_path)
    plist = config["installed_dir"] / f"{JOB}.plist"
    plist.write_bytes(plist.read_bytes().replace(b"<plist", b"<!--bad--dash--><plist", 1))
    assert "scheduled-run-outcomes.strict-plist" in _labels(_run(config))


def test_scheduler_manifest_drift_warns(tmp_path: Path):
    config = _setup(tmp_path)
    scheduler = config["scheduled_tasks_paths"][0]
    scheduler.write_text(json.dumps({"tasks": [{
        "launchd_id": JOB,
        "plist_file": "wrong.plist",
    }]}), encoding="utf-8")
    assert "scheduled-run-outcomes.scheduler-drift" in _labels(_run(config))


def test_process_offset_drift_warns(tmp_path: Path):
    config = _setup(tmp_path)
    config["process_offset"] = timedelta(hours=-4)
    assert "scheduled-run-outcomes.process-tz-drift" in _labels(_run(config))


def test_registry_loader_is_found_beside_the_pack_not_under_repo_root(tmp_path: Path):
    """The canonical loader (tools/operator_registry_check.py) ships beside
    agentica_core/ in both layouts. The public export has no Governance/ dir at all,
    so resolving it through repo_root read outside the distribution and every test
    in this file reported "operator registry unreadable" there (2026-09-06). A
    repo_root with no Governance/tools/ under it must still load the registry."""
    config = _setup(tmp_path)
    elsewhere = tmp_path / "not-a-repo"
    elsewhere.mkdir()
    config["repo_root"] = elsewhere
    assert _labels(_run(config)) == ["scheduled-run-outcomes.healthy"]
