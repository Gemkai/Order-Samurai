"""Tests for doctor's scheduled surface: execution/doctor.py --write-state.

Audit 2026-09-01 finding B2: doctor reported FAIL=1 continuously from 2026-08-23
and nothing saw it, because stdout on a manual invocation was its only surface.
These tests pin the properties the alert path depends on: every gating FAIL row
reaches the file, the file is written even when doctor fails, and recording the
run can never change the verdict doctor just computed.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from execution import doctor  # noqa: E402

_NOW = datetime(2026, 9, 2, 6, 45, tzinfo=timezone.utc)


def _family(name: str, rows: list[dict], *, gating: bool = True) -> doctor._Family:
    return doctor._Family(name, lambda: rows, gating=gating)


def _ok(label: str) -> dict:
    return {"status": "OK", "label": label, "detail": "fine"}


def _fail(label: str, detail: str = "broken") -> dict:
    return {"status": "FAIL", "label": label, "detail": detail}


def test_every_gating_fail_row_reaches_the_state_file(tmp_path):
    """One line per FAIL is the whole product: hitl_alerts renders these verbatim."""
    report = doctor.run_report((
        _family("alpha", [_ok("a.ok"), _fail("a.broken", "detail one")]),
        _family("beta", [_fail("b.broken", "detail two")]),
    ))
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json", now=_NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["counts"] == {"OK": 1, "WARN": 0, "FAIL": 2, "ERROR": 0}
    assert payload["exit_code"] == 1
    assert [f["label"] for f in payload["fails"]] == ["a.broken", "b.broken"]
    assert [f["detail"] for f in payload["fails"]] == ["detail one", "detail two"]
    assert [f["family"] for f in payload["fails"]] == ["alpha", "beta"]
    assert payload["generated_at"] == _NOW.isoformat()


def test_a_clean_run_records_an_empty_fail_list_not_an_absent_file(tmp_path):
    """A clean doctor must still write. An absent file means 'doctor did not run',
    and the alert path treats that as its own WARN — conflating the two would let a
    dead scheduler read as a healthy system."""
    report = doctor.run_report((_family("alpha", [_ok("a.ok")]),))
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json", now=_NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.exists()
    assert payload["fails"] == []
    assert payload["exit_code"] == 0


def test_non_gating_fail_rows_are_visible_without_becoming_gating_failures(tmp_path):
    """Spectator FAILs reach consumers but do not alter the gate verdict."""
    report = doctor.run_report((_family("spectator", [_fail("s.broken")], gating=False),))
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json", now=_NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["fails"] == [{"family": "spectator", "label": "s.broken",
                                 "detail": "broken", "status": "FAIL", "gating": False}]
    assert payload["counts"]["FAIL"] == 0
    assert payload["exit_code"] == 0


def test_run_families_keeps_its_three_tuple_contract():
    """run_report is the new surface; run_families is what every existing caller
    and test uses, and it must keep returning exactly (lines, counts, exit_code)."""
    families = (_family("alpha", [_ok("a.ok"), _fail("a.broken")]),)
    lines, counts, exit_code = doctor.run_families(families)
    report = doctor.run_report(families)

    assert lines == report.lines
    assert counts == report.counts
    assert exit_code == report.exit_code == 1


def test_state_write_failure_does_not_change_the_exit_code(tmp_path, monkeypatch, capsys):
    """doctor's verdict is the product; recording it is a side effect. A read-only
    state dir must not turn a FAIL into a crash or a clean run into a failure."""
    monkeypatch.setattr(doctor, "CHECK_FAMILIES", (_family("alpha", [_fail("a.broken")]),))
    monkeypatch.setattr(doctor, "STATE_PATH", tmp_path / "nope" / "doctor_last.json")

    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(doctor, "write_state", boom)
    assert doctor.main(["--write-state"]) == 1
    assert "FAILED to write" in capsys.readouterr().err


def test_the_file_is_written_even_when_doctor_fails(tmp_path, monkeypatch):
    """The FAIL case is the only case this mechanism exists for."""
    monkeypatch.setattr(doctor, "CHECK_FAMILIES", (_family("alpha", [_fail("a.broken")]),))
    monkeypatch.setattr(doctor, "STATE_PATH", tmp_path / "doctor_last.json")

    assert doctor.main(["--write-state"]) == 1
    assert json.loads((tmp_path / "doctor_last.json").read_text())["fails"][0]["label"] == "a.broken"


def test_without_the_flag_nothing_is_written(tmp_path, monkeypatch):
    """A manual /doctor run must stay read-only: the audit's own inventory sweep
    mutated state by invoking scripts that do work as a side effect (B11)."""
    monkeypatch.setattr(doctor, "CHECK_FAMILIES", (_family("alpha", [_ok("a.ok")]),))
    monkeypatch.setattr(doctor, "STATE_PATH", tmp_path / "doctor_last.json")

    assert doctor.main([]) == 0
    assert not (tmp_path / "doctor_last.json").exists()


def test_help_neither_runs_the_checks_nor_writes(tmp_path, monkeypatch):
    """`--help` used to execute the entire sweep (audit B11). argparse now answers
    before any family runs."""
    ran: list[str] = []

    def tripwire() -> list[dict]:
        ran.append("x")
        return []

    monkeypatch.setattr(doctor, "CHECK_FAMILIES", (doctor._Family("alpha", tripwire, gating=True),))
    monkeypatch.setattr(doctor, "STATE_PATH", tmp_path / "doctor_last.json")

    with pytest.raises(SystemExit) as exc:
        doctor.main(["--help"])
    assert exc.value.code == 0
    assert ran == []
    assert not (tmp_path / "doctor_last.json").exists()


def test_concurrent_writers_do_not_publish_each_others_payloads(tmp_path):
    """A fixed `.tmp` sibling was safe against torn reads but not against concurrent
    writers: two overlapping runs shared one temp path, so a FAIL run could write its
    temp, have a clean run overwrite it, then publish the clean payload and report
    success — losing the FAIL while claiming to have recorded it. Doctor has a daily
    job, a manual entry point and a sensei-cycle caller, so overlap is reachable."""
    import threading

    path = tmp_path / "doctor_last.json"
    reports = [
        doctor.run_report((_family(f"f{i}", [_fail(f"fail.{i}", f"detail {i}")]),))
        for i in range(8)
    ]
    errors: list[Exception] = []

    def write(report):
        try:
            doctor.write_state(report, path=path)
        except Exception as exc:                      # noqa: BLE001 - recorded, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(r,)) for r in reports]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Whichever run won, the file is one run's payload in full — never a blend, and
    # never another run's content published under this run's success.
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload["fails"]) == 1
    label = payload["fails"][0]["label"]
    assert payload["fails"][0]["detail"] == f"detail {label.split('.')[1]}"
    # No scratch files survive a completed write.
    assert [p.name for p in tmp_path.iterdir()] == ["doctor_last.json"]


def test_the_state_file_is_world_readable_like_the_rest_of_state(tmp_path):
    """tempfile.mkstemp creates 0600. Every other file under state/ is 0644, and this
    one is a health report with no secrets — an owner-only file would be an
    unannounced narrowing introduced by the atomic-write fix rather than a decision."""
    import stat

    report = doctor.run_report((_family("alpha", [_ok("a.ok")]),))
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json", now=_NOW)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & stat.S_IRGRP
    assert mode & stat.S_IROTH


# ── crash isolation and the ERROR tier (audit S1/S2, plan M3.1–M3.2) ────────────

def _boom(name: str = "alpha") -> doctor._Family:
    def runner() -> list[dict]:
        raise RuntimeError("policy file vanished mid-run")
    return doctor._Family(name, runner, gating=True)


def test_a_crashing_family_does_not_take_the_rest_of_the_run_with_it(tmp_path):
    """Before crash isolation a raising family aborted the whole sweep, so one broken
    check silenced every LATER check — and under the launchd job, --write-state never
    ran either. cli.py has done this since it was written; doctor had not."""
    report = doctor.run_report((
        _boom("alpha"),
        _family("beta", [_ok("b.ok")]),
        _family("gamma", [_fail("g.broken", "real defect")]),
    ))
    labels = [line.split("]", 1)[1].split(":")[0].strip() for line in report.lines]
    assert "alpha.family-crashed" in labels
    assert "b.ok" in labels            # a later family still ran
    assert "g.broken" in labels        # and a later FAIL is still reported


def test_a_crash_is_an_error_not_a_failure(tmp_path):
    """ERROR says "I could not run"; FAIL says "I ran and it is wrong". A crash that
    reported FAIL would be a verdict nobody computed."""
    report = doctor.run_report((_boom(),))
    assert report.counts["ERROR"] == 1
    assert report.counts["FAIL"] == 0
    assert report.fails == []
    assert report.errors[0]["label"] == "alpha.family-crashed"
    assert "RuntimeError" in report.errors[0]["detail"]


def test_a_crash_alone_exits_two(tmp_path):
    assert doctor.run_report((_boom(),)).exit_code == 2


def test_a_real_failure_still_outranks_a_crash(tmp_path):
    """Callers that gate on `exit == 1` must keep seeing a genuine defect."""
    report = doctor.run_report((_boom(), _family("beta", [_fail("b.broken")])))
    assert report.exit_code == 1
    assert report.counts["ERROR"] == 1
    assert report.counts["FAIL"] == 1


def test_a_crash_in_a_non_gating_family_is_still_counted(tmp_path):
    """Spectator families do not gate on FAIL, but "could not run" is never a
    spectator's private business — it is the reason a row is missing."""
    def runner() -> list[dict]:
        raise OSError("no such file")
    report = doctor.run_report((doctor._Family("spectator", runner, gating=False),))
    assert report.counts["ERROR"] == 1
    assert report.exit_code == 2


def test_the_state_file_records_errors_separately_from_fails(tmp_path):
    report = doctor.run_report((_boom(), _family("beta", [_fail("b.broken", "d")])))
    path = doctor.write_state(report, path=tmp_path / "doctor_last.json", now=_NOW)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [f["label"] for f in payload["fails"]] == ["b.broken"]
    assert [e["label"] for e in payload["errors"]] == ["alpha.family-crashed"]
