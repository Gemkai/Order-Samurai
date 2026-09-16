"""Escalation that resolves or retires — coverage review R4.1 / R4.2 (2026-09-02).

R4.1: an item that expired unreviewed stays in the digest, with its age, until a human
writes a decision. R4.2: a launchd job failing day after day climbs three tiers — banner
(day 1), digest subject (day 3), HITL "auto-disable?" item (day 7) — and an unanswered item
disables the job and records the revert command.

The retirement transaction is the delicate part: `launchctl disable` survives logins, so
every failure window after it lands must leave the job restored or loudly unreconciled.
Those tests plant each window rather than describing it.

All launchctl calls are faked; nothing here touches the live fleet.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ORDER_SAMURAI = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "hitl_alerts_escalation", ORDER_SAMURAI / "bin" / "hitl_alerts.py"
)
assert _SPEC and _SPEC.loader
hitl_alerts = importlib.util.module_from_spec(_SPEC)
sys.modules["hitl_alerts_escalation"] = hitl_alerts
_SPEC.loader.exec_module(hitl_alerts)

NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
# The registry is this machine's fleet, not package data: the public export ships
# the loader (tools/operator_registry_check.py) but not the file, so the two tests
# below that assert against LIVE registry rows skip where there is none. Every
# other test here plants its own rows and runs in both layouts.
_HAS_REGISTRY = hitl_alerts.OPERATOR_REGISTRY_PATH.is_file()
_NO_REGISTRY = pytest.mark.skipif(
    not _HAS_REGISTRY,
    reason="no operator registry in this distribution (the export ships the loader, not the fleet)",
)
REGISTRY = (json.loads(hitl_alerts.OPERATOR_REGISTRY_PATH.read_text(encoding="utf-8"))
            if _HAS_REGISTRY else {"automations": []})
PROTECTED_JOBS = sorted(
    row["id"].removeprefix("com.") for row in REGISTRY["automations"]
    if row.get("auto_disable") is False
)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch):
    monkeypatch.setitem(sys.modules, "secret_env", SimpleNamespace(lookup=lambda name: None))


@pytest.fixture
def repo(monkeypatch, tmp_path: Path) -> Path:
    """A queue under <root>/state/ so bushido_engine (which keys everything off the parent
    of state/) and hitl_alerts agree on one file."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "hitl_queue.json").write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setattr(hitl_alerts, "_ROOT", tmp_path)
    monkeypatch.setattr(hitl_alerts, "QUEUE_PATH", state_dir / "hitl_queue.json")
    monkeypatch.setattr(hitl_alerts, "STATE_PATH", state_dir / "hitl_alert_state.json")
    monkeypatch.setattr(hitl_alerts, "PATCH_DIR", state_dir)
    monkeypatch.setattr(hitl_alerts, "BACKLOG_PATH", state_dir / "PROPOSED_BACKLOG.json")
    monkeypatch.setattr(hitl_alerts, "FLEET_PROBE_PATH", tmp_path / "fleet_probe.json")
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", state_dir / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "RETIREMENTS_PATH", state_dir / "fleet_retirements.json")
    # Plant the operator registry too. Auto-disable fails CLOSED when the registry
    # is unreadable, so every test that expects a silence sweep to actually disable
    # a job was leaning on the LIVE file being present -- invisible in the repo,
    # 31 failures in the public export (which ships no registry). The planted copy
    # carries the live rows where there are any, so repo semantics are unchanged.
    registry_path = tmp_path / "operator_registry.json"
    registry_path.write_text(json.dumps({"automations": REGISTRY["automations"]}),
                             encoding="utf-8")
    monkeypatch.setattr(hitl_alerts, "OPERATOR_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(hitl_alerts, "_now", lambda: NOW)
    (state_dir / "doctor_last.json").write_text(json.dumps({
        "generated_at": NOW.isoformat(), "exit_code": 0,
        "counts": {"OK": 30, "WARN": 0, "FAIL": 0}, "fails": []}), encoding="utf-8")
    return tmp_path


def _write_queue(repo: Path, items: list[dict]) -> None:
    (repo / "state" / "hitl_queue.json").write_text(json.dumps({"items": items}), encoding="utf-8")


def _queue(repo: Path) -> list[dict]:
    return json.loads((repo / "state" / "hitl_queue.json").read_text(encoding="utf-8"))["items"]


def _probe(repo: Path, failing: list[str], age_hours: float = 0.1) -> None:
    (repo / "fleet_probe.json").write_text(json.dumps({
        "generated_at": (NOW - timedelta(hours=age_hours)).isoformat(),
        "failing_jobs": failing, "unreachable_services": []}), encoding="utf-8")


def _expired(qid: str, days_ago: float, **over) -> dict:
    item = {"id": qid, "status": "expired", "source": "reflex", "skill": "pip-safe-upgrade",
            "command": "/pip-safe-upgrade", "pillar": "sword",
            "enqueued_at": _iso(days_ago + 28), "expired_at": _iso(days_ago),
            "expired_reason": "stale"}
    item.update(over)
    return item


def _fleet_item(job: str, status: str = "pending", expires_in_days: float = 7, **over) -> dict:
    item = {"id": f"hitl-{job[-6:]}", "status": status, "source": "fleet",
            "skill": "launchd-retire", "command": "", "pillar": "bow",
            "backlog_id": f"fleet-disable:{job}", "enqueued_at": _iso(7),
            "expires_at": (NOW + timedelta(days=expires_in_days)).isoformat(),
            "on_expire": "disable_launchd_job", "context": "ctx"}
    item.update(over)
    return item


JOB = "agentica.vault-autocommit"
LABEL = f"com.{JOB}"
#: Literal, not imported from the module under test: the fake must not depend on
#: the code it is used to falsify. test_no_such_label_rc_is_pinned holds them equal.
NO_SUCH_LABEL = 113


def _fake_launchctl(monkeypatch, *, label: str = LABEL, disabled: bool = False,
                    loaded: bool = True, disable_rc: int = 0, bootout_rc: int = 0,
                    enable_rc: int = 0, bootstrap_rc: int = 0,
                    disable_exc: BaseException | None = None,
                    bootout_exc: BaseException | None = None,
                    print_rc: int | None = None, print_disabled_rc: int = 0,
                    apply_bootout: bool = True, apply_bootstrap: bool = True):
    """launchctl fake that MODELS state rather than returning canned answers: `disabled`
    and `loaded` are mutated by the commands, so `print-disabled`/`print` read back what
    actually happened and a verification assertion means something.

    Planting: `*_rc`/`*_exc` fail a command; `apply_*=False` makes one report success
    without taking effect; `print_rc` plants a diagnostic failure of the reader itself
    (rc 1 = permission error, distinct from rc 113 = no such label)."""
    world = {label: {"disabled": disabled, "loaded": loaded}}
    calls: list[list[str]] = []

    def st(lbl: str) -> dict:
        return world.setdefault(lbl, {"disabled": False, "loaded": True})

    def rc(code: int):
        return SimpleNamespace(returncode=code, stderr="boom" if code else "", stdout="")

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[0] != "launchctl":
            return SimpleNamespace(returncode=0, stderr=b"dispatched\n", stdout=b"")
        verb, tail = argv[1], argv[-1]
        lbl = tail.rsplit("/", 1)[-1]
        if verb == "disable":
            if disable_exc is not None:
                raise disable_exc
            if disable_rc == 0:
                st(lbl)["disabled"] = True
            return rc(disable_rc)
        if verb == "bootout":
            if bootout_exc is not None:
                raise bootout_exc
            if bootout_rc == 0 and apply_bootout:
                st(lbl)["loaded"] = False
            return rc(bootout_rc)
        if verb == "enable":
            if enable_rc == 0:
                st(lbl)["disabled"] = False
            return rc(enable_rc)
        if verb == "bootstrap":
            lbl = Path(tail).name.removesuffix(".plist")
            if bootstrap_rc == 0 and apply_bootstrap:
                st(lbl)["loaded"] = True
            return rc(bootstrap_rc)
        if verb == "print-disabled":
            if print_disabled_rc:
                return rc(print_disabled_rc)
            body = "".join(f'\t"{k}" => {"true" if v["disabled"] else "false"}\n'
                           for k, v in world.items())
            return SimpleNamespace(returncode=0, stderr="", stdout=body)
        if verb == "print":
            if print_rc is not None:
                return rc(print_rc)
            return rc(0 if st(lbl)["loaded"] else NO_SUCH_LABEL)
        return rc(0)

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    return SimpleNamespace(calls=calls, world=world)


def _verbs(fake) -> list[str]:
    """Mutating launchctl verbs only — the reader's probes are noise for these tests."""
    return [c[1] for c in fake.calls
            if c and c[0] == "launchctl" and c[1] not in ("print", "print-disabled")]


def _retirements(repo: Path) -> list[dict]:
    return json.loads((repo / "state" / "fleet_retirements.json").read_text())["items"]


# ── R4.1: expiry is a state ───────────────────────────────────────────────────

def test_every_expired_item_is_listed_regardless_of_age(repo):
    _write_queue(repo, [_expired("hitl-old", 45), _expired("hitl-new", 2)])
    _, expired, _ = hitl_alerts.load_queue()
    assert [i["id"] for i in expired] == ["hitl-old", "hitl-new"]   # oldest first


def test_retired_items_leave_the_expired_list(repo):
    _write_queue(repo, [_expired("hitl-old", 45, status="retired", retired_at=_iso(0))])
    assert hitl_alerts.load_queue() == ([], [], [])


def test_digest_names_expired_without_decision_with_ages_and_the_command(repo, monkeypatch):
    _write_queue(repo, [_expired("hitl-old", 45), _expired("hitl-new", 2)])
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda s, b, t: captured.update(subject=s, body=b) or True)
    assert hitl_alerts.do_email(force=False) == 0
    body = captured["body"]
    assert "EXPIRED WITHOUT DECISION (2)" in body
    assert "hitl-old pip-safe-upgrade (reflex/sword) — expired 2026-07-19, 6wk ago" in body
    assert "bushido_check.py --retire <id> --reason" in body
    assert "EXPIRED UNREVIEWED in the last" not in body


# ── R4.2: fleet tiers ─────────────────────────────────────────────────────────

def test_first_seen_is_recorded_and_forgotten_on_recovery():
    state: dict = {}
    days = hitl_alerts.track_fleet_first_seen(state, ["agentica.a", "agentica.b"], NOW)
    assert days == {"agentica.a": 0, "agentica.b": 0}
    state["fleet_first_seen"]["agentica.a"] = _iso(8)
    days = hitl_alerts.track_fleet_first_seen(state, ["agentica.a"], NOW)
    assert days == {"agentica.a": 8}
    assert "agentica.b" not in state["fleet_first_seen"]     # recovered -> forgotten
    days = hitl_alerts.track_fleet_first_seen(state, ["agentica.b"], NOW)
    assert days == {"agentica.b": 0}                          # recurrence starts at day 0


def test_tiers_follow_the_day_thresholds():
    tiers = hitl_alerts.fleet_tiers({"x": 0, "y": 3, "z": 7, "w": 20})
    assert {j: t["tier"] for j, t in tiers.items()} == {"x": "banner", "y": "digest", "z": "hitl", "w": "hitl"}


def test_subject_names_the_longest_failing_job_from_day_3():
    assert hitl_alerts.fleet_subject_clause(hitl_alerts.fleet_tiers({"x": 2})) == ""
    clause = hitl_alerts.fleet_subject_clause(hitl_alerts.fleet_tiers({"x": 3, "agentica.vault-autocommit": 9}))
    assert clause == "fleet: agentica.vault-autocommit failing 9d (+1 more)"
    d = hitl_alerts.decide_email(force=False, today="2026-09-02", last_email_date=None,
                                 pending=[], expired=[], patches=[], fleet_clause=clause)
    assert d.send is True
    assert d.subject == "[Order Samurai] fleet: agentica.vault-autocommit failing 9d (+1 more)"
    d2 = hitl_alerts.decide_email(force=False, today="2026-09-02", last_email_date=None,
                                  pending=[{"id": "p"}], expired=[], patches=[], fleet_clause=clause)
    assert d2.subject.startswith("[Order Samurai] 1 approval(s) waiting · fleet: ")


def test_day_7_raises_one_hitl_item_per_job_with_a_deadline(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    _probe(repo, ["agentica.vault-autocommit", "agentica.review-audit"])
    state = {"fleet_first_seen": {"agentica.vault-autocommit": _iso(8), "agentica.review-audit": _iso(2)}}
    lines = hitl_alerts._maybe_fleet_escalations(state)
    assert any("agentica.vault-autocommit failing 8d — HITL item raised" in ln for ln in lines)
    items = _queue(repo)
    assert len(items) == 1
    it = items[0]
    assert it["source"] == "fleet" and it["command"] == "" and it["status"] == "pending"
    assert it["backlog_id"] == "fleet-disable:agentica.vault-autocommit"
    assert it["expires_at"] == (NOW + timedelta(days=7)).isoformat()
    assert it["on_expire"] == "disable_launchd_job"
    assert "com.agentica.vault-autocommit" in it["context"]
    assert fake.calls == []                                 # raising is not disabling
    # Second cycle: deduped, nothing new.
    assert not any("HITL item raised" in ln for ln in hitl_alerts._maybe_fleet_escalations(state))
    assert len(_queue(repo)) == 1


def test_escalation_never_raises_when_the_queue_read_fails_right_after_enqueuing(repo, monkeypatch):
    """_maybe_fleet_escalations's own docstring promises it "Never raises — a broken queue
    or probe reports and moves on." The queue is read twice per cycle when a new day-7 item
    is raised: once before enqueue_fleet_hitl(), once after (to pick up what was just
    written). If that SECOND read hits a transient QueueReadError, an uncaught exception
    here propagates out of do_notify() before _save_state() runs, dropping this cycle's
    banner/tier bookkeeping entirely instead of degrading gracefully like the first read."""
    _fake_launchctl(monkeypatch)
    _probe(repo, ["agentica.vault-autocommit"])
    state = {"fleet_first_seen": {"agentica.vault-autocommit": _iso(8)}}

    real_queue_items = hitl_alerts._queue_items
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise hitl_alerts.QueueReadError("disk hiccup")
        return real_queue_items()

    monkeypatch.setattr(hitl_alerts, "_queue_items", flaky)

    lines = hitl_alerts._maybe_fleet_escalations(state)   # must not raise
    assert any("HITL item raised" in ln for ln in lines)
    assert any("unreadable" in ln.lower() for ln in lines)


def test_a_recent_reject_suppresses_re_raising_for_14_days(repo, monkeypatch):
    _fake_launchctl(monkeypatch)
    _probe(repo, ["agentica.vault-autocommit"])
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", status="rejected", rejected_at=_iso(3))])
    state = {"fleet_first_seen": {"agentica.vault-autocommit": _iso(30)}}
    hitl_alerts._maybe_fleet_escalations(state)
    assert [i["status"] for i in _queue(repo)] == ["rejected"]
    # ...but after the re-decide window it is asked again.
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", status="rejected", rejected_at=_iso(20))])
    hitl_alerts._maybe_fleet_escalations(state)
    assert sorted(i["status"] for i in _queue(repo)) == ["pending", "rejected"]


def test_stale_probe_never_advances_tiers(repo, monkeypatch):
    _fake_launchctl(monkeypatch)
    _probe(repo, ["agentica.vault-autocommit"], age_hours=5)
    state = {"fleet_first_seen": {"agentica.vault-autocommit": _iso(30)}}
    lines = hitl_alerts._maybe_fleet_escalations(state)
    assert any("probe stale" in ln for ln in lines)
    assert _queue(repo) == []


# ── R4.2: the sweep ───────────────────────────────────────────────────────────

def test_unanswered_item_past_expiry_disables_the_job_and_records_the_revert(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-0.5)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("auto-disabled after silence" in ln for ln in lines)
    uid = hitl_alerts.os.getuid()
    assert _verbs(fake) == ["disable", "bootout"]
    assert fake.world[LABEL] == {"disabled": True, "loaded": False}
    row = _queue(repo)[0]
    assert row["status"] == "retired"
    assert "auto-disabled" in row["retired_reason"] and "revert: launchctl enable" in row["retired_reason"]
    rec = json.loads((repo / "state" / "fleet_retirements.json").read_text())["items"]
    assert rec[0]["label"] == "com.agentica.vault-autocommit"
    assert rec[0]["revert"] == (f"launchctl enable gui/{uid}/com.agentica.vault-autocommit && "
                                f"launchctl bootstrap gui/{uid} ~/Library/LaunchAgents/com.agentica.vault-autocommit.plist")
    # Chained like any human decision.
    ledger = (repo / "state" / "review_ledger.jsonl").read_text().strip().splitlines()
    assert json.loads(ledger[-1])["action"] == "retire"


def test_item_before_expiry_is_left_alone(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=0.5)])
    assert hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW) == []
    assert _queue(repo)[0]["status"] == "pending"
    assert fake.calls == []


def test_kill_switch_expires_instead_of_disabling(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    monkeypatch.setenv("HITL_FLEET_AUTO_DISABLE", "false")
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("not auto-disabled" in ln for ln in lines)
    assert fake.calls == []
    row = _queue(repo)[0]
    assert row["status"] == "expired" and "HITL_FLEET_AUTO_DISABLE=false" in row["expired_reason"]
    # ...and therefore it is now in the EXPIRED WITHOUT DECISION list.
    assert [i["id"] for i in hitl_alerts.load_queue()[1]] == [row["id"]]


@_NO_REGISTRY
@pytest.mark.parametrize("job", PROTECTED_JOBS)
def test_alert_carriers_are_never_auto_disabled(repo, monkeypatch, job):
    fake = _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item(job, expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("auto_disable:false" in ln for ln in lines)
    assert fake.calls == []
    assert _queue(repo)[0]["status"] == "expired"


@_NO_REGISTRY
def test_critical_carriers_remain_protected_by_registry():
    assert {
        "agentica.os-hitl-bridge", "agentica.morning-joe-refresh",
        "agentica.escalation-canary", "claude.reflex-poller",
        "agentica.substrate-preflight-watchdog", "agentica.brain3-file-bridge",
    } <= set(PROTECTED_JOBS)


def test_registry_failure_prevents_every_silent_auto_disable(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    monkeypatch.setattr(hitl_alerts, "OPERATOR_REGISTRY_PATH", repo / "missing.json")
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("fails closed" in line for line in lines)
    assert fake.calls == []


def test_human_approval_disables_now_and_completes_the_item(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch, label="com.agentica.hitl-notifier")
    _write_queue(repo, [_fleet_item("agentica.hitl-notifier", status="approved", approved_at=_iso(0))])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("disabled on approval" in ln for ln in lines)
    assert _verbs(fake) == ["disable", "bootout"]       # a human's approval overrides the guard
    assert _queue(repo)[0]["status"] == "done"
    rec = json.loads((repo / "state" / "fleet_retirements.json").read_text())["items"]
    assert rec[0]["reason"] == "human approved disable"


def test_a_failed_disable_leaves_the_item_pending_and_says_so(repo, monkeypatch):
    _fake_launchctl(monkeypatch, disable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("will retry" in ln and "exited 1" in ln for ln in lines)
    assert _queue(repo)[0]["status"] == "pending"
    rec = json.loads((repo / "state" / "fleet_retirements.json").read_text())["items"]
    assert rec[0]["state"] == "failed" and "exited 1" in rec[0]["detail"]


def test_a_disable_that_never_landed_is_not_rolled_back(repo, monkeypatch):
    """Rollback is owed only once launchd was actually changed. A refused `disable`
    changed nothing, so re-enabling would be noise — and would make `rolled_back`
    meaningless as a journal state."""
    fake = _fake_launchctl(monkeypatch, disable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _verbs(fake) == ["disable"]


def test_bootout_failure_is_not_reported_as_retired(repo, monkeypatch):
    _fake_launchctl(monkeypatch, bootout_rc=5)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("bootout exited 5" in line for line in lines)
    assert _queue(repo)[0]["status"] == "pending"
    assert _retirements(repo)[0]["state"] != "disabled"


def test_bootout_failure_leaves_the_job_enabled_and_loaded(repo, monkeypatch):
    """`launchctl disable` survives logins, so a bootout failure that returns without
    compensating retires the job for good while the operator is told "will retry"."""
    fake = _fake_launchctl(monkeypatch, bootout_rc=5)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _verbs(fake) == ["disable", "bootout", "enable", "bootstrap"]
    assert any("job restored" in line for line in lines)
    assert _retirements(repo)[0]["state"] == "rolled_back"


def test_a_bootout_that_times_out_re_enables_the_job(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch, bootout_exc=hitl_alerts.subprocess.TimeoutExpired(
        cmd=["launchctl", "bootout"], timeout=hitl_alerts.SUBPROCESS_TIMEOUT))
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL]["disabled"] is False
    assert any("bootout failed" in line and "job restored" in line for line in lines)
    assert _retirements(repo)[0]["state"] == "rolled_back"


def test_a_bootout_that_reports_success_without_stopping_the_job_is_caught(repo, monkeypatch):
    """Both commands exit zero and only the state read catches that the job never
    stopped — the case exit codes alone cannot see."""
    fake = _fake_launchctl(monkeypatch, apply_bootout=False)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("half-applied: disabled=True, loaded=True" in line for line in lines)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _retirements(repo)[0]["state"] == "rolled_back"


# ── the launchd state reader is tri-state ─────────────────────────────────────

def test_no_such_label_rc_is_pinned():
    assert hitl_alerts.LAUNCHCTL_NO_SUCH_LABEL == NO_SUCH_LABEL


def test_a_print_error_that_is_not_no_such_label_is_never_read_as_unloaded(repo, monkeypatch):
    """rc 1 is `Operation not permitted`, not proof of absence; only rc 113 means the
    label is gone. Reading rc 1 as unloaded finalized a retirement that never happened."""
    fake = _fake_launchctl(monkeypatch, print_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("print exited 1, not 113" in line for line in lines)
    assert _queue(repo)[0]["status"] == "pending"
    assert _retirements(repo)[0]["state"] != "disabled"
    assert "enable" in _verbs(fake)


def test_an_unreadable_disabled_list_never_finalizes_the_transaction(repo, monkeypatch):
    _fake_launchctl(monkeypatch, print_disabled_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("print-disabled exited 1" in line for line in lines)
    assert _queue(repo)[0]["status"] == "pending"


def test_a_bootstrap_that_reports_failure_but_restored_the_job_is_a_rollback(repo, monkeypatch):
    """`bootstrap` returns nonzero for an already-loaded job. Judging the restore by exit
    code alone reported `rollback_failed` over a job that was verifiably running."""
    fake = _fake_launchctl(monkeypatch, bootout_rc=5, bootstrap_rc=37, apply_bootout=False)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _retirements(repo)[0]["state"] == "rolled_back"


def test_a_bootstrap_that_exits_zero_without_loading_is_not_a_rollback(repo, monkeypatch):
    """The job really was stopped, and the restore only claims to have started it: an
    exit code alone would call this `rolled_back` over a job that is still down."""
    fake = _fake_launchctl(monkeypatch, apply_bootstrap=False)
    _break_writes_after(monkeypatch, {"disabled"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": False}
    assert _retirements(repo)[0]["state"] == "rollback_failed"


# ── retry settles, it does not restart ────────────────────────────────────────

def test_a_retry_settles_an_already_disabled_job_instead_of_restarting_it(repo, monkeypatch):
    """The recoverable state after a crash between the journal write and the queue
    decision. The sweep must finish the transaction, not undo it."""
    fake = _fake_launchctl(monkeypatch, disabled=True, loaded=False)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _verbs(fake) == []                       # nothing re-run, nothing undone
    assert fake.world[LABEL] == {"disabled": True, "loaded": False}
    assert _queue(repo)[0]["status"] == "retired"
    assert any("auto-disabled after silence" in line for line in lines)
    assert _retirements(repo)[0]["state"] == "disabled"


def test_a_retry_of_an_approved_item_settles_an_already_disabled_job(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch, label="com.agentica.hitl-notifier",
                           disabled=True, loaded=False)
    _write_queue(repo, [_fleet_item("agentica.hitl-notifier", status="approved", approved_at=_iso(0))])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _verbs(fake) == []
    assert _queue(repo)[0]["status"] == "done"


# ── nothing after the disable escapes past the compensation ───────────────────

def _break_writes_after(monkeypatch, states: set[str]) -> dict:
    """Plant an OSError on the journal write for the given states, as a full disk would.
    Returns a handle whose `armed` key can be cleared to let later writes through."""
    real = hitl_alerts._record_retirement
    handle = {"armed": True}

    def flaky(job, queue_id, reason, detail, state, rows=None):
        if handle["armed"] and state in states:
            raise OSError("disk full")
        return real(job, queue_id, reason, detail, state, rows)

    monkeypatch.setattr(hitl_alerts, "_record_retirement", flaky)
    return handle


def test_a_failed_disabled_journal_write_restores_the_job(repo, monkeypatch):
    """Without a boundary the exception escapes between the disable and the queue
    decision, leaving the job stopped and the journal still saying `intent`."""
    fake = _fake_launchctl(monkeypatch)
    _break_writes_after(monkeypatch, {"disabled"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _verbs(fake) == ["disable", "bootout", "enable", "bootstrap"]
    assert any("journal write failed" in line for line in lines)
    assert _queue(repo)[0]["status"] == "pending"
    assert _retirements(repo)[0]["state"] == "rolled_back"


def test_a_failed_disabled_journal_write_never_settles_the_queue(repo, monkeypatch):
    """An unrecorded disable is indistinguishable from one that never happened, so the
    decision must not be written against it."""
    _fake_launchctl(monkeypatch)
    _break_writes_after(monkeypatch, {"disabled"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert (repo / "state" / "review_ledger.jsonl").exists() is False


def test_a_journal_write_failing_during_rollback_still_restores_the_machine(repo, monkeypatch):
    """Losing the journal entirely must not cost the compensation too."""
    fake = _fake_launchctl(monkeypatch)
    _break_writes_after(monkeypatch, {"disabled", "rolled_back", "rollback_failed"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _retirements(repo)[0]["state"] == "intent"


def test_an_intent_row_left_by_a_lost_write_is_reconciled_by_the_next_sweep(repo, monkeypatch):
    """The durable end of the previous test: journal `intent`, machine disabled. The
    next sweep observes the end state and settles rather than re-running anything."""
    _fake_launchctl(monkeypatch)
    handle = _break_writes_after(monkeypatch, {"disabled", "rolled_back", "rollback_failed"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)          # leaves state="intent"
    assert _retirements(repo)[0]["state"] == "intent"
    handle["armed"] = False
    fake = _fake_launchctl(monkeypatch, disabled=True, loaded=False)
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _verbs(fake) == []
    assert _queue(repo)[0]["status"] == "retired"
    assert _retirements(repo)[0]["state"] == "disabled"


def test_a_rollback_that_fails_records_the_hand_revert_command(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _verbs(fake) == ["disable", "bootout", "enable", "bootstrap"]
    row = _retirements(repo)[0]
    assert row["state"] == "rollback_failed"
    assert "restore unverified" in row["detail"] and "enable=1" in row["detail"]
    assert any(row["revert"] in line for line in lines)


def test_a_rollback_that_fails_still_leaves_the_item_pending(repo, monkeypatch):
    """The job needs a human, but the decision was never made — so the item stays
    askable rather than being settled on a disable that failed."""
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _queue(repo)[0]["status"] == "pending"


def test_a_rolled_back_attempt_is_not_digested_as_auto_disabled(repo, monkeypatch):
    """`load_retirements` feeds the AUTO-DISABLED digest section and the operator-attention
    gap check; a compensated attempt disabled nothing and must not appear there."""
    _fake_launchctl(monkeypatch, bootout_rc=5)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert hitl_alerts.load_retirements() == []


# ── an unreconciled rollback reaches a human ──────────────────────────────────

def _strand_a_job(repo, monkeypatch) -> None:
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _retirements(repo)[0]["state"] == "rollback_failed"


def test_a_queue_decision_that_raises_restores_the_job(repo, monkeypatch):
    """A raising ledger is a refused decision, not an escape hatch: the exception used
    to leave the service retired with the approval still pending."""
    fake = _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    monkeypatch.setattr(hitl_alerts._bushido(), "review_hitl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("queue disk full")))
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _verbs(fake) == ["disable", "bootout", "enable", "bootstrap"]
    assert _queue(repo)[0]["status"] == "pending"
    assert _retirements(repo)[0]["state"] == "rolled_back"
    assert any("queue decision raised OSError: queue disk full" in line for line in lines)


def test_a_queue_decision_that_raises_keeps_the_exception_in_the_journal(repo, monkeypatch):
    _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    monkeypatch.setattr(hitl_alerts._bushido(), "review_hitl",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger closed")))
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert "queue decision raised RuntimeError: ledger closed" in _retirements(repo)[0]["detail"]


def test_a_failed_rollback_is_listed_as_unreconciled(repo, monkeypatch):
    _strand_a_job(repo, monkeypatch)
    stuck = hitl_alerts.load_unreconciled_retirements()
    assert [r["label"] for r in stuck] == [LABEL]
    assert hitl_alerts.load_retirements() == []      # and never as a completed retirement


def test_notify_fails_and_banners_while_a_job_may_still_be_disabled(repo, monkeypatch):
    """The pending-item banner is deduped for 24h and says nothing about the machine, so
    a failed rollback needs its own banner and a nonzero exit for the launchd job."""
    _strand_a_job(repo, monkeypatch)
    _probe(repo, [])
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    banners: list[tuple[str, str, str]] = []
    monkeypatch.setattr(hitl_alerts, "_dispatch_banner",
                        lambda t, b, s: banners.append((t, b, s)) or True)
    assert hitl_alerts.do_notify() == 1
    assert any("rollback incomplete" in t.lower() for t, _, _ in banners)
    assert any(LABEL in b for _, b, _ in banners)


def test_notify_still_banners_when_the_rollback_row_itself_cannot_be_written(repo, monkeypatch):
    """The journal-dead case: the durable consumer re-reads the journal, so the one
    incident whose persistence failed is exactly the one it cannot see. The incident
    this invocation carries out of the sweep is what keeps notify loud."""
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _break_writes_after(monkeypatch, {"rollback_failed"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    _probe(repo, [])
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    banners: list[tuple[str, str, str]] = []
    monkeypatch.setattr(hitl_alerts, "_dispatch_banner",
                        lambda t, b, s: banners.append((t, b, s)) or True)
    assert hitl_alerts.do_notify() == 1
    assert any("rollback incomplete" in t.lower() for t, _, _ in banners)
    assert _retirements(repo)[0]["state"] != "rollback_failed"   # the write really failed
    assert hitl_alerts.load_unreconciled_retirements() == []     # ...so the journal is blind
    assert any(LABEL in b for _, b, _ in banners)                 # ...and notify said it anyway


def test_a_state_save_failure_cannot_suppress_the_rollback_warning(repo, monkeypatch):
    """One full disk causes both symptoms at once, and notify used to return on the
    alert-state write — losing the warning that a job may be sitting disabled. The
    state save and the rollback report share no data; neither may gate the other."""
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _break_writes_after(monkeypatch, {"rollback_failed"})
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    _probe(repo, [])
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    monkeypatch.setattr(hitl_alerts, "_save_state", lambda st: (_ for _ in ()).throw(
        hitl_alerts.AlertStateError("disk full")))
    banners: list[tuple[str, str, str]] = []
    monkeypatch.setattr(hitl_alerts, "_dispatch_banner",
                        lambda t, b, s: banners.append((t, b, s)) or True)
    assert hitl_alerts.do_notify() == 1
    assert any("rollback incomplete" in t.lower() for t, _, _ in banners)
    assert any(LABEL in b for _, b, _ in banners)


def test_a_state_save_failure_alone_still_fails_the_run(repo, monkeypatch):
    _fake_launchctl(monkeypatch)
    _probe(repo, [])
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    monkeypatch.setattr(hitl_alerts, "_save_state", lambda st: (_ for _ in ()).throw(
        hitl_alerts.AlertStateError("disk full")))
    assert hitl_alerts.do_notify() == 1


def test_a_reconciled_retry_leaves_no_stale_alert_in_the_same_process(repo, monkeypatch):
    """The incident lives in the sweep's own list, so a retry that reconciles the
    journal cannot be shadowed by an in-memory alert nothing is able to clear."""
    handle = _break_writes_after(monkeypatch, {"rollback_failed"})
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    incidents: list[dict] = []
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW, incidents=incidents)
    assert [i["label"] for i in incidents] == [LABEL]     # nondurable, journal refused it
    assert hitl_alerts.load_unreconciled_retirements() == []

    handle["armed"] = False                               # the disk recovers
    _fake_launchctl(monkeypatch, disabled=True, loaded=True)
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)       # the retry settles it
    assert _queue(repo)[0]["status"] == "retired"
    assert _retirements(repo)[0]["state"] == "disabled"

    _probe(repo, [])
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    banners: list[tuple[str, str, str]] = []
    monkeypatch.setattr(hitl_alerts, "_dispatch_banner",
                        lambda t, b, s: banners.append((t, b, s)) or True)
    assert hitl_alerts.do_notify() == 0                   # a later notify is clean
    assert not any("rollback incomplete" in t.lower() for t, _, _ in banners)


def test_the_digest_never_inherits_a_nondurable_incident(repo, monkeypatch):
    """`do_email` runs in its own process and reads durable rows only."""
    _break_writes_after(monkeypatch, {"rollback_failed"})
    _fake_launchctl(monkeypatch, bootout_rc=5, enable_rc=1)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    incidents: list[dict] = []
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW, incidents=incidents)
    assert incidents and hitl_alerts.load_unreconciled_retirements() == []
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda s, b, t: captured.update(subject=s, body=b) or True)
    assert hitl_alerts.do_email(force=True) == 0
    assert "ROLLBACK INCOMPLETE" not in captured["body"]


def test_an_unresolved_rollback_forces_an_unprompted_daily_email(repo, monkeypatch):
    """The queue can look completely clear while a job sits disabled by nobody's
    decision, and an unforced digest used to say "nothing pending" and send nothing."""
    _strand_a_job(repo, monkeypatch)
    _write_queue(repo, [])
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda s, b, t: captured.update(subject=s, body=b) or True)
    assert hitl_alerts.do_email(force=False) == 0
    assert captured["subject"] == "[Order Samurai] ROLLBACK INCOMPLETE — 1 job(s) may still be disabled"
    assert LABEL in captured["body"]


def test_an_unresolved_rollback_older_than_the_digest_window_is_still_reported(repo, monkeypatch):
    """Time passing is not resolution: only a later sweep upserting the row is."""
    _fake_launchctl(monkeypatch)
    (repo / "state" / "fleet_retirements.json").write_text(json.dumps({"items": [
        {"job": JOB, "label": LABEL, "queue_id": "hitl-old", "state": "rollback_failed",
         "updated_at": _iso(8), "reason": "unanswered 7d", "detail": "restore unverified",
         "revert": "launchctl enable gui/501/" + LABEL}]}))
    assert [r["label"] for r in hitl_alerts.load_unreconciled_retirements()] == [LABEL]
    assert hitl_alerts.load_retirements() == []      # and still not a completed retirement


def test_a_later_sweep_reconciling_the_row_clears_the_unreconciled_report(repo, monkeypatch):
    """The terminal state that does resolve it, so the report cannot latch forever."""
    _strand_a_job(repo, monkeypatch)
    _fake_launchctl(monkeypatch, disabled=True, loaded=False)
    hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert _retirements(repo)[0]["state"] == "disabled"
    assert hitl_alerts.load_unreconciled_retirements() == []


def test_the_digest_names_a_job_that_may_still_be_disabled(repo, monkeypatch):
    _strand_a_job(repo, monkeypatch)
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda s, b, t: captured.update(subject=s, body=b) or True)
    assert hitl_alerts.do_email(force=True) == 0
    assert "ROLLBACK INCOMPLETE — MAY STILL BE DISABLED (1)" in captured["body"]
    assert f"revert: launchctl enable gui/{hitl_alerts.os.getuid()}/{LABEL}" in captured["body"]


def test_queue_refusal_rolls_back_and_keeps_a_durable_record(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    _write_queue(repo, [_fleet_item("agentica.vault-autocommit", expires_in_days=-1)])
    monkeypatch.setattr(hitl_alerts._bushido(), "review_hitl", lambda *args, **kwargs: False)
    lines = hitl_alerts.sweep_fleet_hitl(_queue(repo), NOW)
    assert any("job restored" in line for line in lines)
    assert _verbs(fake)[-2:] == ["enable", "bootstrap"]
    assert fake.world[LABEL] == {"disabled": False, "loaded": True}
    assert _queue(repo)[0]["status"] == "pending"
    assert _retirements(repo)[0]["state"] == "rolled_back"


def test_digest_shows_tiers_and_auto_disabled_with_revert(repo, monkeypatch):
    _probe(repo, ["agentica.vault-autocommit", "agentica.soji-cycle"])
    (repo / "state" / "hitl_alert_state.json").write_text(json.dumps({
        "fleet_first_seen": {"agentica.vault-autocommit": _iso(9), "agentica.soji-cycle": _iso(1)}}))
    (repo / "state" / "fleet_retirements.json").write_text(json.dumps({"items": [
        {"job": "agentica.brain3-dream", "label": "com.agentica.brain3-dream", "queue_id": "hitl-x",
         "disabled_at": _iso(1), "reason": "unanswered 7d — auto-disabled", "detail": "d",
         "revert": "launchctl enable gui/501/com.agentica.brain3-dream && launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.agentica.brain3-dream.plist"}]}))
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda s, b, t: captured.update(subject=s, body=b) or True)
    assert hitl_alerts.do_email(force=False) == 0
    assert captured["subject"] == "[Order Samurai] fleet: agentica.vault-autocommit failing 9d"
    body = captured["body"]
    assert "agentica.vault-autocommit — 9d, tier hitl (HITL item raised" in body
    assert "agentica.soji-cycle — 1d, tier banner (digest subject at day 3)" in body
    assert "AUTO-DISABLED in the last 7d (1):" in body
    assert "revert: launchctl enable gui/501/com.agentica.brain3-dream" in body


def test_notify_runs_the_escalations_and_prints_them(repo, monkeypatch):
    fake = _fake_launchctl(monkeypatch)
    _probe(repo, ["agentica.vault-autocommit"])
    (repo / "state" / "hitl_alert_state.json").write_text(json.dumps({
        "fleet_first_seen": {"agentica.vault-autocommit": _iso(8)},
        "last_fleet_signature": "job:agentica.vault-autocommit", "last_fleet_banner_at": NOW.isoformat()}))
    monkeypatch.setattr(hitl_alerts, "_refresh_fleet_probe", lambda: None)
    assert hitl_alerts.do_notify() == 0
    assert len(_queue(repo)) == 1 and _queue(repo)[0]["source"] == "fleet"
    saved = json.loads((repo / "state" / "hitl_alert_state.json").read_text())
    assert saved["fleet_first_seen"] == {"agentica.vault-autocommit": _iso(8)}
    assert fake.calls == []


def test_workitem_round_trips_expires_at_and_on_expire(tmp_path):
    be = hitl_alerts._bushido()
    (tmp_path / "state").mkdir()
    qid = be.enqueue_hitl(be.WorkItem(skill="s", source="fleet", backlog_id="fleet-disable:x",
                                       expires_at="2026-09-09T00:00:00+00:00", on_expire="disable_launchd_job"),
                          be.Tier.HITL, tmp_path)
    plain = be.enqueue_hitl(be.WorkItem(skill="s2", source="reflex"), be.Tier.HITL, tmp_path)
    rows = {i["id"]: i for i in json.loads((tmp_path / "state" / "hitl_queue.json").read_text())["items"]}
    assert rows[qid]["expires_at"] == "2026-09-09T00:00:00+00:00"
    assert rows[qid]["on_expire"] == "disable_launchd_job"
    assert "expires_at" not in rows[plain] and "on_expire" not in rows[plain]   # historical shape kept
