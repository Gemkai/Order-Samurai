"""Failure-injection tests for HITL queue reading and delivery accounting."""
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
    "hitl_alerts", ORDER_SAMURAI / "bin" / "hitl_alerts.py"
)
assert _SPEC and _SPEC.loader
hitl_alerts = importlib.util.module_from_spec(_SPEC)
sys.modules["hitl_alerts"] = hitl_alerts
_SPEC.loader.exec_module(hitl_alerts)


FIXED_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    """_resend_key() falls back to the macOS Keychain (2026-08-16 M1). Without this,
    any test that deletes RESEND_API_KEY from the env resolves the REAL key and hits
    the live Resend API (observed: real 403 from api.resend.com in a dry-run test).
    Tests that exercise the fallback override sys.modules['secret_env'] themselves."""
    monkeypatch.setitem(
        sys.modules, "secret_env", SimpleNamespace(lookup=lambda name: None)
    )


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    queue = tmp_path / "hitl_queue.json"
    state = tmp_path / "hitl_alert_state.json"
    monkeypatch.setattr(hitl_alerts, "QUEUE_PATH", queue)
    monkeypatch.setattr(hitl_alerts, "STATE_PATH", state)
    monkeypatch.setattr(hitl_alerts, "PATCH_DIR", tmp_path)
    monkeypatch.setattr(hitl_alerts, "BACKLOG_PATH", tmp_path / "PROPOSED_BACKLOG.json")
    monkeypatch.setattr(hitl_alerts, "FLEET_PROBE_PATH", tmp_path / "fleet_probe.json")
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    # Ambient default: doctor ran this morning and found nothing. Without a planted
    # file the real repo's state/doctor_last.json leaked into every test here, and an
    # absent one is deliberately NOT silence (it reports "doctor has never run"), so
    # either would make these queue-focused tests assert against doctor's mood.
    _plant_doctor(tmp_path)
    return queue, state


def _plant_doctor(tmp_path: Path, *, fails: list[dict] | None = None,
                  generated_at: datetime | None = None,
                  counts: dict | None = None) -> Path:
    path = tmp_path / "doctor_last.json"
    path.write_text(json.dumps({
        "generated_at": (generated_at or FIXED_NOW).isoformat(),
        "exit_code": 1 if fails else 0,
        "counts": counts or {"OK": 30, "WARN": 2, "FAIL": len(fails or [])},
        "fails": fails or [],
    }), encoding="utf-8")
    return path


def _pending() -> dict:
    return {
        "id": "hitl-test-1",
        "status": "pending",
        "command": "/repair",
        "pillar": "Wisdom",
        "metric_id": "arts:Test",
        "enqueued_at": "2026-08-01T12:00:00+00:00",
    }


def test_missing_queue_is_an_error_not_an_empty_queue(isolated) -> None:
    with pytest.raises(hitl_alerts.QueueReadError, match="cannot read"):
        hitl_alerts.load_queue()


@pytest.mark.parametrize("raw", ["{truncated", "{}", '{"items": {}}'])
def test_malformed_queue_is_an_error(isolated, raw: str) -> None:
    queue, _ = isolated
    queue.write_text(raw, encoding="utf-8")
    with pytest.raises(hitl_alerts.QueueReadError):
        hitl_alerts.load_queue()


def test_valid_empty_queue_remains_distinct_from_read_failure(isolated) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    assert hitl_alerts.load_queue() == ([], [], [])


def test_notify_nonzero_exit_is_not_recorded_as_delivered(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    original = {"last_pending_ids": []}
    state.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(
        hitl_alerts.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=3, stderr=b"notifier unavailable"),
    )

    assert hitl_alerts.do_notify() == 1
    assert json.loads(state.read_text()) == original


def test_notify_success_is_recorded_only_after_zero_exit(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    monkeypatch.setattr(
        hitl_alerts.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b"dispatched\n"),
    )

    assert hitl_alerts.do_notify() == 0
    saved = json.loads(state.read_text())
    assert saved["last_pending_ids"] == ["hitl-test-1"]
    assert saved["last_banner_at"] == FIXED_NOW.isoformat()


def test_notify_zero_exit_without_dispatch_ack_is_not_recorded(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        hitl_alerts.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stderr=b"suppressed (set NUDGE_DESKTOP_NOTIFY=true / raise severity)\n",
        ),
    )

    assert hitl_alerts.do_notify() == 1
    assert json.loads(state.read_text()) == {}


def test_notify_queue_read_failure_returns_nonzero_without_an_approval_banner(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowed 2026-09-02. This used to assert that an unreadable queue dispatched
    NOTHING. That was too strong once doctor became an independent alarm: a corrupt
    approval queue was switching off the doctor banner as well, which is one file
    silencing the mechanism M1 exists to build. What must still hold is that no
    APPROVAL banner is sent — zero pending is an artefact of the failed read, not an
    observation — and that the run still exits non-zero."""
    queue, _ = isolated
    queue.write_text("not-json", encoding="utf-8")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 1
    assert not any("approval(s) waiting" in a for c in calls for a in c)


def test_email_queue_read_failure_returns_nonzero_and_reports_the_queue(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowed for the same reason. The digest now goes out and SAYS the queue could
    not be read, rather than not arriving at all: a daily report that silently stops
    is indistinguishable from a healthy quiet day."""
    queue, _ = isolated
    queue.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    sent: list[tuple] = []
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda subject, body, to: sent.append((subject, body)) or True)

    assert hitl_alerts.do_email(force=False) == 1
    assert "QUEUE UNREADABLE" in sent[0][1]


def test_email_transport_failure_does_not_advance_delivery_date(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(hitl_alerts, "_send_mail_app", lambda *args: False)

    assert hitl_alerts.do_email(force=False) == 1
    assert json.loads(state.read_text()) == {}


def test_empty_queue_does_not_claim_an_email_was_sent(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    state.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    assert hitl_alerts.do_email(force=False) == 0
    assert json.loads(state.read_text()) == {}


def test_delivered_email_with_state_write_failure_returns_nonzero(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setattr(hitl_alerts, "_send_mail_app", lambda *args: True)

    def fail_save(state: dict) -> None:
        raise hitl_alerts.AlertStateError("injected disk failure")

    monkeypatch.setattr(hitl_alerts, "_save_state", fail_save)
    assert hitl_alerts.do_email(force=False) == 1


def test_state_write_error_is_not_swallowed(isolated) -> None:
    _, state = isolated
    state.mkdir()
    with pytest.raises(hitl_alerts.AlertStateError, match="cannot persist"):
        hitl_alerts._save_state({"last_email_date": "2026-08-02"})


class _ResendResponse:
    def __init__(self, body: dict) -> None:
        self.status = 200
        self._body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@pytest.mark.parametrize("body,expected", [({}, False), ({"id": "email-123"}, True)])
def test_resend_requires_a_confirmed_message_id(
    monkeypatch: pytest.MonkeyPatch, body: dict, expected: bool
) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _ResendResponse(body))
    assert hitl_alerts._send_resend("subject", "body", "owner@example.test", "key") is expected


def test_resend_request_carries_a_non_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-08-17: Cloudflare in front of api.resend.com blocks Python's default
    urllib User-Agent (error 1010) before Resend's own auth ever runs, which
    masqueraded as a plain 403 regardless of key validity."""
    import urllib.request

    captured: dict = {}

    def fake_urlopen(req, *args, **kwargs):
        captured["user_agent"] = req.get_header("User-agent")
        return _ResendResponse({"id": "email-123"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    hitl_alerts._send_resend("subject", "body", "owner@example.test", "key")
    assert captured["user_agent"]
    assert "python-urllib" not in captured["user_agent"].lower()


# ── pending-patch surfacing (propose-only lane consumers, 2026-08-08) ────────


import os  # noqa: E402  (test helpers below need utime)


def _plant_patch(tmp_path: Path, name: str = "pending_remediation_metric_arts_Test.patch") -> Path:
    p = tmp_path / name
    p.write_text("diff --git a/x b/x\n", encoding="utf-8")
    one_day_before_fixed_now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(p, (one_day_before_fixed_now, one_day_before_fixed_now))
    return p


def test_planted_pending_patch_is_returned_by_load_queue(isolated, tmp_path: Path) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_patch(tmp_path)
    _, _, patches = hitl_alerts.load_queue()
    assert [p["name"] for p in patches] == ["pending_remediation_metric_arts_Test.patch"]


def test_patch_surface_kill_switch_hides_patches(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_patch(tmp_path)
    monkeypatch.setenv("HITL_PATCH_SURFACE", "false")
    assert hitl_alerts.load_queue() == ([], [], [])


def test_digest_dry_run_includes_planted_patch_with_age(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    state.write_text("{}", encoding="utf-8")
    _plant_patch(tmp_path)
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    captured: dict = {}

    def fake_send(subject: str, body: str, to: str) -> bool:
        captured.update(subject=subject, body=body, to=to)
        return True

    monkeypatch.setattr(hitl_alerts, "_send_mail_app", fake_send)
    # A pending patch alone is a send-trigger: the propose-only lane's output must
    # not go back to having zero consumers on an empty approval queue.
    assert hitl_alerts.do_email(force=False) == 0
    assert "VALIDATED PATCHES AWAITING REVIEW (1):" in captured["body"]
    assert "pending_remediation_metric_arts_Test.patch · waiting 1d" in captured["body"]
    assert "review_pending_patch.py" in captured["body"]


def test_notify_banner_fires_for_patch_only_backlog(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    # Fleet health is a separate concern from this test's patch-backlog assertion;
    # disabling it keeps the subprocess call count meaningful (no fleet_probe refresh,
    # no fleet banner) without coupling this test to that unrelated feature.
    monkeypatch.setenv("HITL_FLEET_ALARM", "false")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stderr=b"dispatched\n")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    _plant_patch(tmp_path)
    assert hitl_alerts.do_notify() == 0
    assert len(calls) == 1
    assert "Order Samurai: 1 approval(s) waiting" in calls[0]
    saved = json.loads(state.read_text())
    assert saved["last_pending_ids"] == ["pending_remediation_metric_arts_Test.patch"]


# ── reflex_patch dedup (a pending patch and its queue enqueue are the same event) ──


def _reflex_patch_pending_item(patch_name: str, item_id: str = "hitl-patch-1") -> dict:
    """A pending hitl_queue.json item shaped like _enqueuePendingPatchHitl's output
    (reflex-engine.ts), including a directory-with-a-space in the embedded path — the
    real Order Samurai root does contain one, and the dedup match must survive it."""
    patch_path = f"<REPO_ROOT>/Governance/Order Samurai/state/{patch_name}"
    return {
        "id": item_id,
        "status": "pending",
        "source": "reflex_patch",
        "command": "/repair",
        "pillar": "Arts",
        "metric_id": "arts:Test",
        "enqueued_at": "2026-08-01T12:00:00+00:00",
        "context": (
            "Validated propose-only remediation patch awaiting review. "
            f"reflex_id=arts:Test skill=/repair patch={patch_path}. "
            "It passed the maker-checker audit and the pytest gate; auto-apply is off, so "
            "the live repo is untouched until a human applies it."
        ),
    }


def test_load_queue_dedupes_reflex_patch_item_against_its_disk_patch(
    isolated, tmp_path: Path
) -> None:
    queue, _ = isolated
    patch = _plant_patch(tmp_path)
    queue.write_text(
        json.dumps({"items": [_reflex_patch_pending_item(patch.name)]}), encoding="utf-8"
    )
    pending, expired, patches = hitl_alerts.load_queue()
    assert pending == []
    assert [p["name"] for p in patches] == [patch.name]


def test_load_queue_keeps_reflex_patch_item_once_its_patch_is_gone(
    isolated, tmp_path: Path
) -> None:
    # Nothing on disk names "...Gone.patch" — either the context never parsed, or the
    # patch was already archived without the queue item being resolved. Either way this
    # is a genuinely unresolved-looking approval, not a duplicate — it must stay visible.
    queue, _ = isolated
    queue.write_text(
        json.dumps({
            "items": [_reflex_patch_pending_item(
                "pending_remediation_metric_arts_Gone.patch", "hitl-patch-2"
            )]
        }),
        encoding="utf-8",
    )
    pending, expired, patches = hitl_alerts.load_queue()
    assert [i["id"] for i in pending] == ["hitl-patch-2"]
    assert patches == []


def test_load_queue_does_not_dedupe_non_reflex_patch_items(
    isolated, tmp_path: Path
) -> None:
    # A normal bushido pending item that happens to mention a patch filename in free
    # text must never be mistaken for that patch's queue duplicate — only source ==
    # 'reflex_patch' is eligible.
    queue, _ = isolated
    patch = _plant_patch(tmp_path)
    item = _pending()
    item["context"] = f"unrelated note mentioning {patch.name}"
    queue.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    pending, expired, patches = hitl_alerts.load_queue()
    assert [i["id"] for i in pending] == ["hitl-test-1"]
    assert [p["name"] for p in patches] == [patch.name]


def test_notify_does_not_double_count_a_reflex_patch_and_its_disk_patch(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    patch = _plant_patch(tmp_path)
    queue.write_text(
        json.dumps({"items": [_reflex_patch_pending_item(patch.name)]}), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stderr=b"dispatched\n")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    assert hitl_alerts.do_notify() == 0
    assert "Order Samurai: 1 approval(s) waiting" in calls[0]


# ── digest delivery-lag alarm ────────────────────────────────────────────────


def _ack_recorder(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stderr=b"dispatched\n")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    return calls


def test_lag_alarm_fires_when_digest_is_stale_with_pending_items(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text(json.dumps({"last_email_date": "2026-07-30"}), encoding="utf-8")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    lag_calls = [c for c in calls if any("LAGGING" in a for a in c)]
    assert len(lag_calls) == 1
    assert any("2026-07-30" in a for a in lag_calls[0])
    assert json.loads(state.read_text())["last_lag_banner_at"] == FIXED_NOW.isoformat()


def test_lag_alarm_stays_quiet_one_day_behind(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text(json.dumps({"last_email_date": "2026-08-01"}), encoding="utf-8")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("LAGGING" in a for c in calls for a in c)
    assert "last_lag_banner_at" not in json.loads(state.read_text())


def test_lag_alarm_stays_quiet_with_nothing_pending(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    state.write_text(json.dumps({"last_email_date": "2026-07-20"}), encoding="utf-8")
    # Fleet health is orthogonal to this test's lag-alarm assertion; disabling it means
    # the only subprocess calls possible are the ones this test actually cares about.
    monkeypatch.setenv("HITL_FLEET_ALARM", "false")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert calls == []


def test_lag_alarm_honors_its_reminder_cadence(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    recent = datetime(2026, 8, 2, 11, 0, tzinfo=timezone.utc).isoformat()
    state.write_text(
        json.dumps({"last_email_date": "2026-07-30", "last_lag_banner_at": recent}),
        encoding="utf-8",
    )
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("LAGGING" in a for c in calls for a in c)
    assert json.loads(state.read_text())["last_lag_banner_at"] == recent


def test_lag_alarm_kill_switch(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text(json.dumps({"last_email_date": "2026-07-30"}), encoding="utf-8")
    monkeypatch.setenv("HITL_LAG_ALARM", "false")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("LAGGING" in a for c in calls for a in c)


# ── fleet health banner (bin/fleet_probe.py -> hitl_alerts.py, 2026-08-09) ───────


def _plant_fleet_probe(
    tmp_path: Path,
    failing: list[str] | None = None,
    unreachable: list[str] | None = None,
    generated_at: str = "2026-08-02T11:55:00+00:00",  # 5min before FIXED_NOW -- fresh
) -> None:
    (tmp_path / "fleet_probe.json").write_text(
        json.dumps({
            "generated_at": generated_at,
            "failing_jobs": failing or [],
            "unreachable_services": unreachable or [],
        }),
        encoding="utf-8",
    )


def test_fleet_banner_fires_on_a_fresh_failure(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync"])
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    fleet_calls = [c for c in calls if any("fleet health" in a for a in c)]
    assert len(fleet_calls) == 1
    assert any("agentica.vault-sync" in a for a in fleet_calls[0])
    saved = json.loads(state.read_text())
    assert saved["last_fleet_signature"] == "job:agentica.vault-sync"
    assert saved["last_fleet_banner_at"] == FIXED_NOW.isoformat()


def test_fleet_banner_does_not_refire_on_an_unchanged_failure(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync"])
    state.write_text(
        json.dumps({"last_fleet_signature": "job:agentica.vault-sync",
                    "last_fleet_banner_at": FIXED_NOW.isoformat()}),
        encoding="utf-8",
    )
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet health" in a for c in calls for a in c)


def test_fleet_banner_refires_when_the_failing_set_changes(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync", "agentica.hitl-digest"])
    state.write_text(
        json.dumps({"last_fleet_signature": "job:agentica.vault-sync",
                    "last_fleet_banner_at": FIXED_NOW.isoformat()}),
        encoding="utf-8",
    )
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert any("fleet health" in a for c in calls for a in c)


def test_fleet_banner_clears_signature_on_recovery_and_refires_on_recurrence(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression test for a real bug caught before it shipped: clearing the stored
    signature on recovery is what lets an IDENTICAL failure set banner again later,
    rather than being silently read as 'unchanged'."""
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    state.write_text(
        json.dumps({"last_fleet_signature": "job:agentica.vault-sync",
                    "last_fleet_banner_at": FIXED_NOW.isoformat()}),
        encoding="utf-8",
    )
    _plant_fleet_probe(tmp_path)  # fleet now clean
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    # The probe-refresh subprocess call still fires (it must, every cycle) — only the
    # BANNER dispatch is what recovery must suppress.
    assert not any("fleet health" in a for c in calls for a in c)
    assert json.loads(state.read_text())["last_fleet_signature"] == ""

    # Same failure recurs immediately after — must banner again, not be suppressed.
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync"])
    assert hitl_alerts.do_notify() == 0
    assert any("fleet health" in a for c in calls for a in c)


def test_fleet_banner_kill_switch(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync"])
    monkeypatch.setenv("HITL_FLEET_ALARM", "false")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet health" in a for c in calls for a in c)


def test_fleet_banner_stays_quiet_when_probe_has_not_run(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    # no fleet_probe.json planted at all
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet health" in a for c in calls for a in c)


def test_stale_probe_banner_fires_even_when_failing_set_is_empty(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed prober freezes on its last snapshot -- which can be a CLEAN one. The
    staleness alarm must not be gated behind a non-empty failing/unreachable set, or a
    prober that dies right after a healthy read goes silently unnoticed forever."""
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, generated_at="2026-08-02T08:00:00+00:00")  # 4h stale, clean
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    stale_calls = [c for c in calls if any("fleet probe is STALE" in a for a in c)]
    assert len(stale_calls) == 1
    assert not any("fleet health" in a for c in calls for a in c)  # no failing set, no separate banner
    saved = json.loads(state.read_text())
    assert saved["last_fleet_stale_banner_at"] == FIXED_NOW.isoformat()


def test_stale_probe_banner_does_not_refire_within_remind_window(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path, generated_at="2026-08-02T08:00:00+00:00")  # 4h stale
    state.write_text(
        json.dumps({"last_fleet_stale_banner_at": FIXED_NOW.isoformat()}),
        encoding="utf-8",
    )
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet probe is STALE" in a for c in calls for a in c)


def test_stale_probe_banner_stays_quiet_when_probe_is_fresh(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    _plant_fleet_probe(tmp_path)  # default generated_at is 5min before FIXED_NOW
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet probe is STALE" in a for c in calls for a in c)


def test_fleet_banner_stays_quiet_on_malformed_probe_data(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text('{"items": []}', encoding="utf-8")
    (tmp_path / "fleet_probe.json").write_text("{truncated", encoding="utf-8")
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    assert not any("fleet health" in a for c in calls for a in c)


def test_load_fleet_probe_missing_file_reports_nothing(isolated) -> None:
    assert hitl_alerts.load_fleet_probe() is None


def test_load_fleet_probe_never_equates_malformed_with_empty(isolated, tmp_path: Path) -> None:
    (tmp_path / "fleet_probe.json").write_text("{truncated", encoding="utf-8")
    probe = hitl_alerts.load_fleet_probe()
    assert probe is not None and "error" in probe


def test_digest_body_renders_fleet_section_with_issues(isolated, tmp_path: Path) -> None:
    _plant_fleet_probe(tmp_path, failing=["agentica.vault-sync"], unreachable=["qdrant"])
    body = hitl_alerts._digest_body([], [], [], None, hitl_alerts.load_fleet_probe())
    assert "FLEET HEALTH:" in body
    assert "launchd job failing: agentica.vault-sync" in body
    assert "service unreachable: qdrant" in body


def test_digest_body_renders_fleet_section_when_clean(isolated, tmp_path: Path) -> None:
    _plant_fleet_probe(tmp_path)
    body = hitl_alerts._digest_body([], [], [], None, hitl_alerts.load_fleet_probe())
    assert "FLEET HEALTH: all launchd jobs + local services OK ✔" in body


def test_digest_body_renders_fleet_section_as_unreadable_on_malformed_probe(
    isolated, tmp_path: Path
) -> None:
    (tmp_path / "fleet_probe.json").write_text("{truncated", encoding="utf-8")
    body = hitl_alerts._digest_body([], [], [], None, hitl_alerts.load_fleet_probe())
    assert "FLEET HEALTH: unreadable" in body


def test_once_per_day_guard_still_skips_even_with_patches_waiting(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": [_pending()]}), encoding="utf-8")
    state.write_text(json.dumps({"last_email_date": "2026-08-02"}), encoding="utf-8")
    _plant_patch(tmp_path)
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    def unexpected(*args):
        raise AssertionError("once-per-day guard must skip before any transport call")

    monkeypatch.setattr(hitl_alerts, "_send_mail_app", unexpected)
    assert hitl_alerts.do_email(force=False) == 0


# ── PROPOSED_BACKLOG read-only section ───────────────────────────────────────


def _plant_backlog(tmp_path: Path) -> None:
    (tmp_path / "PROPOSED_BACKLOG.json").write_text(json.dumps({"items": [
        {"id": "A-1", "approved": False, "triaged_at": "2026-07-08 triage (user-ratified)"},
        {"id": "A-2", "approved": False},
        {"id": "A-3", "approved": True, "triaged_at": "2026-06-01 triage"},
    ]}), encoding="utf-8")


def test_backlog_summary_counts_unapproved_and_oldest_age(isolated, tmp_path: Path) -> None:
    _plant_backlog(tmp_path)
    assert hitl_alerts.load_backlog_summary() == {"pending": 2, "oldest_days": 25}


def test_backlog_summary_missing_file_reports_nothing(isolated) -> None:
    assert hitl_alerts.load_backlog_summary() is None


def test_backlog_summary_never_equates_malformed_with_empty(isolated, tmp_path: Path) -> None:
    (tmp_path / "PROPOSED_BACKLOG.json").write_text("{truncated", encoding="utf-8")
    summary = hitl_alerts.load_backlog_summary()
    assert summary is not None and "error" in summary


def test_backlog_kill_switch(isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _plant_backlog(tmp_path)
    monkeypatch.setenv("HITL_BACKLOG_SURFACE", "false")
    assert hitl_alerts.load_backlog_summary() is None


def test_digest_body_renders_backlog_section(isolated, tmp_path: Path) -> None:
    _plant_backlog(tmp_path)
    body = hitl_alerts._digest_body([], [], [], hitl_alerts.load_backlog_summary())
    assert "PROPOSED_BACKLOG (read-only): 2 pending (approved:false) · oldest 3wk" in body


# ── Mail.app launch preamble ─────────────────────────────────────────────────


def test_mail_transport_ensures_mail_running_before_composing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The AppleScript `launch` event -600s when Mail is closed (2026-08-09), so the
    # pre-launch contract is: _ensure_mail_running() succeeds BEFORE osascript runs,
    # and the AppleScript itself carries no launch preamble.
    calls: list = []
    captured: dict = {}

    monkeypatch.setattr(
        hitl_alerts, "_ensure_mail_running", lambda: calls.append("ensure") or True
    )

    def fake_run(argv, **kwargs):
        calls.append("osascript")
        captured["script"] = kwargs.get("input", "")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    assert hitl_alerts._send_mail_app("s", "b", "owner@example.test") is True
    assert calls == ["ensure", "osascript"]
    assert "to launch" not in captured["script"]
    assert "make new outgoing message" in captured["script"]


def test_mail_transport_fails_fast_when_mail_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hitl_alerts, "_ensure_mail_running", lambda: False)

    def fake_run(argv, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("osascript must not run when Mail cannot start")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    assert hitl_alerts._send_mail_app("s", "b", "owner@example.test") is False


def test_mail_launch_kill_switch_skips_ensure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hitl_alerts,
        "_ensure_mail_running",
        lambda: (_ for _ in ()).throw(AssertionError("ensure must be skipped")),
    )

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", fake_run)
    monkeypatch.setenv("HITL_MAIL_LAUNCH", "false")
    assert hitl_alerts._send_mail_app("s", "b", "owner@example.test") is True


# ── decide_email / email_channel (M7.4: pure decisions, no env or network) ────
# Extracted from do_email, which could only be exercised by setting env vars,
# writing state files, and stubbing a mail transport. The two skip rules exist to
# stop a scheduled job mailing noise, so they are worth testing directly.

def _decision(**over):
    base = dict(force=False, today="2026-08-16", last_email_date=None,
                pending=[], expired=[], patches=[])
    base.update(over)
    return hitl_alerts.decide_email(**base)


def test_digest_is_skipped_when_one_already_went_out_today():
    decision = _decision(last_email_date="2026-08-16", pending=[{"id": "a"}])

    assert decision.send is False
    assert "already sent 2026-08-16" in decision.skip_message


def test_yesterdays_send_does_not_block_todays():
    decision = _decision(last_email_date="2026-08-15", pending=[{"id": "a"}])

    assert decision.send is True


def test_nothing_to_report_sends_nothing():
    decision = _decision()

    assert decision.send is False
    assert "no email today" in decision.skip_message


def test_force_overrides_an_already_sent_digest():
    decision = _decision(force=True, last_email_date="2026-08-16")

    assert decision.send is True


def test_force_sends_even_with_an_empty_queue():
    decision = _decision(force=True)

    assert decision.send is True
    assert decision.subject.endswith("queue clear")


def test_subject_counts_pending_and_patches_but_not_expired():
    """Expired items are reported in the body but are not awaiting anyone."""
    decision = _decision(pending=[{"id": "a"}], patches=[{"name": "p"}],
                         expired=[{"id": "x"}, {"id": "y"}])

    assert decision.subject == "[Order Samurai] 2 approval(s) waiting"


def test_expired_items_alone_still_warrant_a_digest():
    decision = _decision(expired=[{"id": "x"}])

    assert decision.send is True
    assert decision.subject.endswith("queue clear")


def test_a_sending_decision_carries_no_skip_message():
    assert _decision(pending=[{"id": "a"}]).skip_message == ""


def test_an_api_key_selects_resend_and_its_absence_selects_mail_app():
    assert hitl_alerts.email_channel("re_abc123") == "resend"
    assert hitl_alerts.email_channel("") == "Mail.app"


class TestResendKeyResolution:
    """_resend_key: env wins; Keychain (secret_env) fallback; empty on failure."""

    def test_env_var_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RESEND_API_KEY", "from-env")
        assert hitl_alerts._resend_key() == "from-env"

    def test_keychain_fallback_when_env_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        fake = SimpleNamespace(lookup=lambda name: {"RESEND_API_KEY": "from-keychain"}.get(name))
        monkeypatch.setitem(sys.modules, "secret_env", fake)
        assert hitl_alerts._resend_key() == "from-keychain"

    def test_empty_when_both_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        fake = SimpleNamespace(lookup=lambda name: None)
        monkeypatch.setitem(sys.modules, "secret_env", fake)
        assert hitl_alerts._resend_key() == ""

    def test_empty_on_resolver_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RESEND_API_KEY", raising=False)

        def _boom(name: str) -> str:
            raise RuntimeError("keychain unavailable")

        monkeypatch.setitem(sys.modules, "secret_env", SimpleNamespace(lookup=_boom))
        assert hitl_alerts._resend_key() == ""


class TestResendMailFallback:
    """A rejected Resend key falls back to Mail.app unless HITL_MAIL_FALLBACK=false."""

    def _arm(self, isolated, monkeypatch: pytest.MonkeyPatch, resend_ok: bool) -> dict:
        queue, state = isolated
        queue.write_text('{"items": []}', encoding="utf-8")
        state.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
        monkeypatch.setenv("RESEND_API_KEY", "resolved-but-maybe-dead")
        calls: dict = {"resend": 0, "mail": 0}

        def fake_resend(subject, body, to, key):
            calls["resend"] += 1
            return resend_ok

        def fake_mail(subject, body, to):
            calls["mail"] += 1
            return True

        monkeypatch.setattr(hitl_alerts, "_send_resend", fake_resend)
        monkeypatch.setattr(hitl_alerts, "_send_mail_app", fake_mail)
        return calls

    def test_resend_failure_falls_back_to_mail_app(self, isolated, monkeypatch) -> None:
        calls = self._arm(isolated, monkeypatch, resend_ok=False)
        assert hitl_alerts.do_email(force=True) == 0
        assert calls == {"resend": 1, "mail": 1}

    def test_resend_success_skips_mail_app(self, isolated, monkeypatch) -> None:
        calls = self._arm(isolated, monkeypatch, resend_ok=True)
        assert hitl_alerts.do_email(force=True) == 0
        assert calls == {"resend": 1, "mail": 0}

    def test_kill_switch_restores_fail_hard(self, isolated, monkeypatch) -> None:
        calls = self._arm(isolated, monkeypatch, resend_ok=False)
        monkeypatch.setenv("HITL_MAIL_FALLBACK", "false")
        assert hitl_alerts.do_email(force=True) == 1
        assert calls == {"resend": 1, "mail": 0}


# ── doctor health surfacing (execution/doctor.py --write-state, 2026-09-02) ──────
#
# Audit finding B2: doctor reported FAIL=1 from 2026-08-23 and nothing read it.
# These tests pin the contract that closes it — every FAIL reaches the banner and
# the digest, and every state in which doctor CANNOT be trusted (missing, stale,
# unreadable) produces its own line instead of silence.

def _doctor_fail(label: str = "claude_architecture.runtime_coupling_boundaries",
                 detail: str = "measured category zeroed (-10)") -> dict:
    return {"family": "claude-architecture", "label": label, "detail": detail}


def test_injected_doctor_fail_reaches_the_notify_banner(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, state = isolated
    queue.write_text(json.dumps({"items": []}))
    _plant_doctor(tmp_path, fails=[_doctor_fail()])
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0

    doctor_calls = [c for c in calls if any("doctor reports" in a for a in c)]
    assert len(doctor_calls) == 1
    assert any("runtime_coupling_boundaries" in a for a in doctor_calls[0])
    saved = json.loads(state.read_text())
    assert "runtime_coupling_boundaries" in saved["last_doctor_signature"]


def test_removing_the_doctor_fail_leaves_the_banner_clean(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror of the test above: a doctor with nothing failing must not banner,
    and must clear its stored signature so a recurrence reads as a fresh incident."""
    queue, state = isolated
    queue.write_text(json.dumps({"items": []}))
    _plant_doctor(tmp_path)  # no fails
    state.write_text(json.dumps({"last_doctor_signature": "DOCTOR FAIL: old — stale",
                                 "last_doctor_banner_at": FIXED_NOW.isoformat()}))
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0

    assert not any("doctor reports" in a for c in calls for a in c)
    assert json.loads(state.read_text())["last_doctor_signature"] == ""


def test_a_stale_doctor_result_warns_rather_than_reading_as_healthy(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead scheduler serving a last-known-clean file is the exact shape of B2.
    Staleness must be reported on its own, not inferred from an absent FAIL list."""
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    doctor = {
        "counts": {"OK": 30, "WARN": 2, "FAIL": 0}, "fails": [],
        "generated_at": (FIXED_NOW - timedelta(hours=50)).isoformat(),
    }
    lines = hitl_alerts.doctor_alert_lines(doctor)
    assert len(lines) == 1
    assert "has not run since" in lines[0]
    assert "50h ago" in lines[0]


def test_a_missing_doctor_result_is_reported_not_silently_skipped() -> None:
    lines = hitl_alerts.doctor_alert_lines({"missing": True})
    assert len(lines) == 1
    assert "has never written" in lines[0]


def test_an_unreadable_doctor_result_is_reported(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    (tmp_path / "doctor_last.json").write_text("{not json", encoding="utf-8")
    lines = hitl_alerts.doctor_alert_lines(hitl_alerts.load_doctor_last())
    assert len(lines) == 1
    assert "unreadable" in lines[0]


def test_a_fresh_clean_doctor_result_produces_no_lines(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The only silent case. If this ever stops being the only one, the banner
    becomes noise and operators learn to ignore it."""
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    _plant_doctor(tmp_path)
    assert hitl_alerts.doctor_alert_lines(hitl_alerts.load_doctor_last()) == []


def test_injected_doctor_fail_reaches_the_digest_body(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    _plant_doctor(tmp_path, fails=[_doctor_fail()])
    body = hitl_alerts._digest_body([], [], [], None, None, hitl_alerts.load_doctor_last())
    assert "DOCTOR:" in body
    assert "runtime_coupling_boundaries" in body


def test_removing_the_doctor_fail_leaves_the_digest_clean(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    _plant_doctor(tmp_path)
    body = hitl_alerts._digest_body([], [], [], None, None, hitl_alerts.load_doctor_last())
    assert "no failing checks" in body
    assert "DOCTOR FAIL" not in body


def test_a_doctor_problem_alone_is_enough_to_send_the_digest() -> None:
    """The "nothing pending -> no email" rule would otherwise swallow a doctor FAIL
    on every day the approval queue happens to be clear."""
    decision = hitl_alerts.decide_email(
        force=False, today="2026-08-02", last_email_date=None,
        pending=[], expired=[], patches=[],
        doctor_problems=["DOCTOR FAIL: something — broke"],
    )
    assert decision.send
    assert "doctor: 1 problem(s)" in decision.subject


def test_a_clean_doctor_does_not_by_itself_trigger_a_digest() -> None:
    decision = hitl_alerts.decide_email(
        force=False, today="2026-08-02", last_email_date=None,
        pending=[], expired=[], patches=[], doctor_problems=[],
    )
    assert not decision.send


def test_the_doctor_alarm_kill_switch_disables_the_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setenv("HITL_DOCTOR_ALARM", "false")
    assert hitl_alerts.load_doctor_last() is None
    assert hitl_alerts.doctor_alert_lines(None) == []


def test_a_stale_doctor_does_not_re_banner_on_every_poll(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notifier polls every 30 minutes. Building the dedup signature from the
    rendered lines put the rounded age ("50h ago") inside it, so the signature changed
    hourly, the 24h re-remind gate never engaged, and one dead scheduler produced a HIGH
    banner roughly every hour indefinitely. Measured before the fix: 8 banners in 14
    polls."""
    queue, state = isolated
    queue.write_text(json.dumps({"items": []}))
    _plant_doctor(tmp_path, generated_at=FIXED_NOW - timedelta(hours=40))
    calls = _ack_recorder(monkeypatch)

    for tick in range(48):                      # 24 hours of 30-minute polls
        monkeypatch.setattr(hitl_alerts, "_now",
                            lambda t=tick: FIXED_NOW + timedelta(minutes=30 * t))
        hitl_alerts.do_notify()

    doctor_banners = [c for c in calls if any("doctor reports" in a for a in c)]
    assert len(doctor_banners) <= 2, f"{len(doctor_banners)} banners for one stale file"
    assert any("has not run since" in a for a in doctor_banners[0])


def test_the_digest_body_actually_carries_the_doctor_section(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through do_email, not _digest_body directly. Without this, dropping
    the doctor argument from the _digest_body call inside do_email leaves every test
    green while the digest decides to send BECAUSE of doctor and never mentions it."""
    queue, state = isolated
    queue.write_text(json.dumps({"items": []}))
    _plant_doctor(tmp_path, fails=[_doctor_fail()])
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    sent: list[tuple] = []
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda subject, body, to: sent.append((subject, body, to)) or True)

    assert hitl_alerts.do_email(force=False) == 0

    subject, body, _ = sent[0]
    assert "doctor: 1 problem(s)" in subject
    assert "DOCTOR:" in body
    assert "runtime_coupling_boundaries" in body


# ── Codex checker findings, 2026-09-02 ──────────────────────────────────────────

def test_an_unreadable_queue_does_not_silence_the_doctor_banner(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """do_notify used to return on a QueueReadError before any doctor reader ran, so
    one unrelated corrupt file switched off the whole "the error signal reaches a
    human" mechanism."""
    queue, _ = isolated                               # the fixture leaves it absent
    assert not queue.exists()                         # -> QueueReadError
    _plant_doctor(tmp_path, fails=[_doctor_fail()])
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 1                # degraded read still non-zero
    doctor_calls = [c for c in calls if any("doctor reports" in a for a in c)]
    assert len(doctor_calls) == 1
    assert any("runtime_coupling_boundaries" in a for a in doctor_calls[0])


def test_an_unreadable_queue_does_not_silence_the_digest(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated                               # the fixture leaves it absent
    assert not queue.exists()                         # -> QueueReadError
    _plant_doctor(tmp_path, fails=[_doctor_fail()])
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    sent: list[tuple] = []
    monkeypatch.setattr(hitl_alerts, "_send_mail_app",
                        lambda subject, body, to: sent.append((subject, body)) or True)

    assert hitl_alerts.do_email(force=False) == 1
    subject, body = sent[0]
    assert "runtime_coupling_boundaries" in body
    assert "QUEUE UNREADABLE" in body
    # The reassuring "No approvals pending" must never read as a clean queue when the
    # queue was never read at all.
    assert body.index("QUEUE UNREADABLE") < body.index("No approvals pending")


def test_a_queue_read_failure_still_suppresses_the_approval_banner(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor gets through; the approval banner must not, because zero pending is an
    artefact of the failed read rather than an observation."""
    queue, _ = isolated                               # the fixture leaves it absent
    assert not queue.exists()                         # -> QueueReadError
    _plant_doctor(tmp_path)                            # doctor clean
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 1
    assert not any("approval(s) waiting" in a for c in calls for a in c)


def test_the_banner_body_names_every_failing_check(tmp_path: Path) -> None:
    """The body was lines[0][:200] plus "(+N more)", so with two failing checks the
    second one's label never reached the human — it appeared only in stdout."""
    body = hitl_alerts._banner_body([
        "DOCTOR FAIL: first.check — detail one",
        "DOCTOR FAIL: second.check — detail two",
    ])
    assert "first.check" in body
    assert "second.check" in body
    assert "not shown" not in body


def test_the_banner_body_says_how_many_it_could_not_fit(tmp_path: Path) -> None:
    """Truncation is inevitable at some width; silently dropping the remainder is not."""
    lines = [f"DOCTOR FAIL: check.{i} — {'x' * 60}" for i in range(12)]
    body = hitl_alerts._banner_body(lines)
    assert len(body) < 600
    assert "not shown" in body
    assert "check.0" in body


def test_a_malformed_fails_row_becomes_a_warn_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`fails: [null]` satisfied the container check and then raised AttributeError
    inside doctor_alert_lines, taking down both alert modes — the opposite of the
    "malformed becomes a WARN" promise. Reported as unreadable rather than dropped:
    a row we cannot parse may be the FAIL that mattered."""
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    (tmp_path / "doctor_last.json").write_text(json.dumps({
        "generated_at": FIXED_NOW.isoformat(),
        "counts": {"OK": 1, "WARN": 0, "FAIL": 1}, "fails": [None]}), encoding="utf-8")

    lines = hitl_alerts.doctor_alert_lines(hitl_alerts.load_doctor_last())
    assert len(lines) == 1
    assert "unreadable" in lines[0]


def test_a_tz_naive_generated_at_does_not_crash_the_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every caller compares against _now(), which is aware, so a naive value raised
    straight out of whichever reader touched it."""
    monkeypatch.setattr(hitl_alerts, "DOCTOR_STATE_PATH", tmp_path / "doctor_last.json")
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    (tmp_path / "doctor_last.json").write_text(json.dumps({
        "generated_at": (FIXED_NOW - timedelta(hours=50)).replace(tzinfo=None).isoformat(),
        "counts": {"OK": 1, "WARN": 0, "FAIL": 0}, "fails": []}), encoding="utf-8")

    lines = hitl_alerts.doctor_alert_lines(hitl_alerts.load_doctor_last())
    assert len(lines) == 1
    assert "has not run since" in lines[0]


def test_the_dispatched_banner_carries_every_failing_check(
    isolated, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through do_notify, not _banner_body directly. Testing the helper
    alone left the wiring unguarded: reverting the call site back to lines[0][:200]
    kept every test green while the second failing check stopped reaching the human."""
    queue, _ = isolated
    queue.write_text(json.dumps({"items": []}))
    _plant_doctor(tmp_path, fails=[
        _doctor_fail("first.check", "detail one"),
        _doctor_fail("second.check", "detail two"),
    ])
    calls = _ack_recorder(monkeypatch)

    assert hitl_alerts.do_notify() == 0
    doctor_calls = [c for c in calls if any("doctor reports" in a for a in c)]
    assert len(doctor_calls) == 1
    payload = " ".join(doctor_calls[0])
    assert "first.check" in payload
    assert "second.check" in payload


# ── Governance dir by layout marker (export gate, 2026-09-06) ─────────────────
# OPERATOR_REGISTRY_PATH / FLEET_PROBE_PATH used a fixed `_ROOT.parent`; in the
# flattened public export that is a directory outside the distribution.

def test_governance_dir_is_the_parent_in_a_nested_checkout(tmp_path):
    gov = tmp_path / "Governance"
    (gov / "agentica_core").mkdir(parents=True)
    pack = gov / "Order Samurai"
    pack.mkdir()
    assert hitl_alerts._governance_dir(pack) == gov


def test_governance_dir_is_the_pack_itself_in_a_flat_export(tmp_path):
    pack = tmp_path / "public-export"
    (pack / "agentica_core").mkdir(parents=True)
    assert hitl_alerts._governance_dir(pack) == pack


def test_a_governance_parent_without_agentica_core_is_not_the_marker(tmp_path):
    pack = tmp_path / "Governance" / "Order Samurai"
    pack.mkdir(parents=True)
    assert hitl_alerts._governance_dir(pack) == pack


def test_registry_path_sits_beside_agentica_core():
    """Live layout or export, the registry is read from the directory holding agentica_core."""
    assert (hitl_alerts.OPERATOR_REGISTRY_PATH.parent.parent / "agentica_core").is_dir()
