"""Failure-injection tests for HITL queue reading and delivery accounting."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
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
    monkeypatch.setattr(hitl_alerts, "_now", lambda: FIXED_NOW)
    return queue, state


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


def test_notify_queue_read_failure_returns_nonzero_without_dispatch(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text("not-json", encoding="utf-8")

    def unexpected(*args, **kwargs):
        raise AssertionError("unreadable queue must not dispatch")

    monkeypatch.setattr(hitl_alerts.subprocess, "run", unexpected)
    assert hitl_alerts.do_notify() == 1


def test_email_queue_read_failure_returns_nonzero_without_transport(
    isolated, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue, _ = isolated
    queue.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("HITL_DIGEST_TO", "owner@example.test")

    def unexpected(*args, **kwargs):
        raise AssertionError("unreadable queue must not send email")

    monkeypatch.setattr(hitl_alerts, "_send_mail_app", unexpected)
    assert hitl_alerts.do_email(force=False) == 1


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
