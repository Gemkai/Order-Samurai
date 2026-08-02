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


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    queue = tmp_path / "hitl_queue.json"
    state = tmp_path / "hitl_alert_state.json"
    monkeypatch.setattr(hitl_alerts, "QUEUE_PATH", queue)
    monkeypatch.setattr(hitl_alerts, "STATE_PATH", state)
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
    assert hitl_alerts.load_queue() == ([], [])


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
