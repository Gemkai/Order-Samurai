"""Push-on-approve: a human approval fires immediately instead of rotting.

Before this, approvals were consumed PULL-style — `_consume_approval` only fired
one if the SAME reflex re-fired while still eligible, through cooldowns tripled by
the efficacy penalty, recovered-metric suppression and REFUTED suppression. Live
evidence: 145 of ~150 code-modifying attempts since 2026-07-21 died in the queue,
4 approvals were ever granted, 3 consumed, one after 3.5 days (hitl-c998eca2 was
approved 2026-08-02 and never executed at all).

The property these tests exist to protect is AT-MOST-ONCE: the push path and the
legacy pull path must never both fire one approval, and a push that fails must not
cost the human's approval. Every HTTP call is mocked — a test that reached the real
API on 127.0.0.1:3001 would fire a real remediation.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
_GOVERNANCE = Path(__file__).resolve().parents[2]
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

from agentica_core import bushido_engine as be  # noqa: E402
from agentica_core.bushido_engine import (  # noqa: E402
    BlastRadius,
    Tier,
    WorkItem,
    _consume_approval,
    decide,
    enqueue_hitl,
    reconcile_stale_approved,
    reconcile_stale_executing,
    review_hitl,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _past_lease_expiry() -> datetime:
    """An instant well past the 2h execution lease, measured from the REAL clock.

    `executing_at` is stamped by review_hitl with datetime.now(), so a lease-expiry
    instant derived from the frozen NOW above stops being in the future once wall
    time passes it -- which silently turned these assertions green-then-red on
    2026-08-15. The reconcile_stale_approved tests below may keep using NOW because
    they write their own explicit approved_at timestamps.
    """
    return datetime.now(timezone.utc) + timedelta(days=7)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_repo(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hitl_queue.json").write_text(
        json.dumps({"schema_version": 1, "items": [], "created_at": "x", "updated_at": "x"}),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    """Hard stop on any unmocked HTTP call — the default endpoint is the LIVE API."""
    def _forbidden(*args, **kwargs):
        raise AssertionError("unmocked HTTP call — a test must never reach the live API")

    monkeypatch.setattr(urllib.request, "urlopen", _forbidden)
    monkeypatch.delenv("BUSHIDO_PUSH_ON_APPROVE", raising=False)
    monkeypatch.delenv("BUSHIDO_PUSH_API_BASE", raising=False)
    monkeypatch.delenv("BUSHIDO_PUSH_TIMEOUT_S", raising=False)
    monkeypatch.delenv("BUSHIDO_APPROVAL_TTL_HOURS", raising=False)


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _mock_urlopen(monkeypatch, *, status: int | None = None, raises: BaseException | None = None):
    """Replace urlopen; return the list that records each (request, timeout) call."""
    calls: list[tuple[urllib.request.Request, float | None]] = []

    def _fake(request, timeout=None, **kwargs):
        calls.append((request, timeout))
        if raises is not None:
            raise raises
        return _FakeResponse(status if status is not None else 202)

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    return calls


def _work_item(**overrides) -> WorkItem:
    base = dict(
        skill="wiki", source="reflex", pillar="arts", command="/wiki",
        metric_id="metric:arts:Wiki_Health_Score",
        blast_radius=BlastRadius.REPO, reversible=True,
    )
    base.update(overrides)
    return WorkItem(**base)


def _enqueue(tmp_repo, **overrides) -> tuple[WorkItem, str]:
    wi = _work_item(**overrides)
    return wi, enqueue_hitl(wi, Tier.QUEUE, tmp_repo)


def _item(tmp_repo, queue_id: str) -> dict:
    data = json.loads((tmp_repo / "state" / "hitl_queue.json").read_text(encoding="utf-8"))
    return [i for i in data["items"] if i["id"] == queue_id][0]


def _events(tmp_repo, name: str) -> list[dict]:
    path = tmp_repo / "state" / "autonomic_events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and json.loads(ln).get("event") == name
    ]


# ── kill switch ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("off", ["false", "0", "no", "off", "FALSE"])
def test_kill_switch_off_is_pull_only(tmp_repo, monkeypatch, off):
    """With BUSHIDO_PUSH_ON_APPROVE off, behaviour is the historical pull-only one:
    the item comes to rest in `approved`, nothing is POSTed (the autouse fixture
    would raise on any call), and the pull path still consumes it."""
    monkeypatch.setenv("BUSHIDO_PUSH_ON_APPROVE", off)
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True

    item = _item(tmp_repo, qid)
    assert item["status"] == "approved"
    assert item["executing_at"] is None
    assert "push_claimed_at" not in item
    assert "dispatched_at" not in item
    assert _events(tmp_repo, "hitl_push") == []

    assert _consume_approval(wi, tmp_repo) == qid


def test_push_is_on_by_default(tmp_repo, monkeypatch):
    """The ratified default is ON — an unset switch must push, not silently no-op."""
    calls = _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    assert len(calls) == 1
    assert _item(tmp_repo, qid)["status"] == "dispatched"


# ── idempotency: push and pull must never both fire one approval ──────────────

def test_successful_push_locks_out_the_pull_path(tmp_repo, monkeypatch):
    """THE property. After a started push, a re-fire of the same reflex must not
    find a consumable approval — otherwise the approval executes twice."""
    calls = _mock_urlopen(monkeypatch, status=202)
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True
    assert _item(tmp_repo, qid)["status"] == "dispatched"
    assert len(calls) == 1

    # Simulated re-fire: both the raw pull primitive and the full decide() entry point.
    assert _consume_approval(wi, tmp_repo) is None
    tier, new_id = decide(wi, tmp_repo)
    assert tier == Tier.QUEUE          # re-queued for a fresh human decision
    assert new_id != qid               # never the already-dispatched item
    assert _item(tmp_repo, qid)["status"] == "dispatched"
    assert len(calls) == 1             # decide() must not push anything


def test_dispatched_item_is_not_reclaimed_by_the_execution_lease(tmp_repo, monkeypatch):
    """`dispatched` is terminal: the 2h lease reconciler must not relabel a
    successfully pushed approval `failed` (which is what leaving it `executing`
    would have caused for every pushed run, since runManual returns no queue id)."""
    _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo)
    review_hitl(qid, tmp_repo, "approve")

    assert reconcile_stale_executing(tmp_repo, now=_past_lease_expiry()) == 0
    assert _item(tmp_repo, qid)["status"] == "dispatched"


def test_already_approved_item_cannot_be_re_approved_into_a_second_push(tmp_repo, monkeypatch):
    calls = _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo)
    assert review_hitl(qid, tmp_repo, "approve") is True
    assert review_hitl(qid, tmp_repo, "approve") is False
    assert len(calls) == 1


# ── the POST itself ───────────────────────────────────────────────────────────

def test_post_targets_the_loopback_manual_run_route_with_a_bounded_timeout(tmp_repo, monkeypatch):
    calls = _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo, command="/wiki")

    review_hitl(qid, tmp_repo, "approve")

    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:3001/api/reflex/exec"
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {"command": "/wiki"}
    assert isinstance(timeout, float) and 0 < timeout <= 60   # never unbounded


def test_push_timeout_is_configurable(tmp_repo, monkeypatch):
    monkeypatch.setenv("BUSHIDO_PUSH_TIMEOUT_S", "1.5")
    calls = _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    assert calls[0][1] == 1.5


@pytest.mark.parametrize("bad", ["", "not-a-number", "0", "-3", "6000"])
def test_malformed_timeout_falls_back_to_a_bounded_default(monkeypatch, bad):
    monkeypatch.setenv("BUSHIDO_PUSH_TIMEOUT_S", bad)
    assert be._push_timeout_s() == be._PUSH_DEFAULT_TIMEOUT_S


@pytest.mark.parametrize("base", [
    "http://evil.example:3001",
    "https://127.0.0.1:3001",
    "http://10.0.0.5:3001",
])
def test_non_loopback_base_never_posts(tmp_repo, monkeypatch, base):
    """A pushed run git-applies inside the staging pipeline — it must never leave
    this machine. A non-loopback base is refused before any socket is opened (the
    autouse fixture raises if urlopen is reached) and the approval is handed back."""
    monkeypatch.setenv("BUSHIDO_PUSH_API_BASE", base)
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True

    assert _item(tmp_repo, qid)["status"] == "approved"
    assert _consume_approval(wi, tmp_repo) == qid


def test_custom_loopback_base_is_honoured(tmp_repo, monkeypatch):
    monkeypatch.setenv("BUSHIDO_PUSH_API_BASE", "http://localhost:4444/")
    calls = _mock_urlopen(monkeypatch, status=200)
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    assert calls[0][0].full_url == "http://localhost:4444/api/reflex/exec"


def test_item_without_a_slash_command_is_never_claimed(tmp_repo):
    """Nothing to push — don't take a lease the route would just 400 back."""
    wi, qid = _enqueue(tmp_repo, command="")

    assert review_hitl(qid, tmp_repo, "approve") is True

    assert _item(tmp_repo, qid)["status"] == "approved"
    assert _consume_approval(wi, tmp_repo) == qid


@pytest.mark.parametrize("action,reason", [("reject", "stale"), ("expire", "recovered")])
def test_reject_and_expire_never_push(tmp_repo, action, reason):
    _, qid = _enqueue(tmp_repo)
    assert review_hitl(qid, tmp_repo, action, reason=reason) is True
    assert _item(tmp_repo, qid)["status"] == ("rejected" if action == "reject" else "expired")


# ── failure handling: a failed push must not cost the approval ────────────────

@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
    urllib.error.URLError(OSError("No route to host")),
])
def test_unreachable_api_returns_the_approval_to_the_pull_path(tmp_repo, monkeypatch, exc):
    _mock_urlopen(monkeypatch, raises=exc)
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True

    item = _item(tmp_repo, qid)
    assert item["status"] == "approved"
    assert item["executing_at"] is None
    assert item["push_claimed_at"] is None
    assert item["push_failure_reason"]
    assert _consume_approval(wi, tmp_repo) == qid   # the approval survived


@pytest.mark.parametrize("code", [400, 404, 409, 422, 429])
def test_route_refusal_returns_the_approval_to_the_pull_path(tmp_repo, monkeypatch, code):
    """Every 4xx the route emits is decided before anything spawns, so no run
    started and the approval is safe to hand back."""
    _mock_urlopen(monkeypatch, raises=urllib.error.HTTPError(
        "http://127.0.0.1:3001/api/reflex/exec", code, "refused", {}, None))
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True

    assert _item(tmp_repo, qid)["status"] == "approved"
    assert _consume_approval(wi, tmp_repo) == qid


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),
    urllib.error.URLError(TimeoutError("timed out")),
    urllib.error.HTTPError("http://127.0.0.1:3001/api/reflex/exec", 500, "boom", {}, None),
])
def test_indeterminate_result_is_left_on_the_execution_lease(tmp_repo, monkeypatch, exc):
    """A timeout or 5xx may mean the request LANDED and the run started. Reverting
    to `approved` there could double-fire, so the item stays on the lease that
    reconcile_stale_executing already owns — recoverable and visible, never twice."""
    _mock_urlopen(monkeypatch, raises=exc)
    wi, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True

    item = _item(tmp_repo, qid)
    assert item["status"] == "executing"
    assert _consume_approval(wi, tmp_repo) is None          # pull path locked out

    assert reconcile_stale_executing(tmp_repo, now=_past_lease_expiry()) == 1
    assert _item(tmp_repo, qid)["status"] == "failed"


def test_non_2xx_success_response_is_indeterminate(tmp_repo, monkeypatch):
    _mock_urlopen(monkeypatch, status=302)
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    assert _item(tmp_repo, qid)["status"] == "executing"


def test_push_failure_is_logged_not_swallowed(tmp_repo, monkeypatch, capsys):
    _mock_urlopen(monkeypatch, raises=urllib.error.URLError(ConnectionRefusedError()))
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    pushes = _events(tmp_repo, "hitl_push")
    assert len(pushes) == 1
    assert pushes[0]["outcome"] == "refused"
    assert pushes[0]["queue_id"] == qid
    assert "unreachable" in pushes[0]["detail"]
    assert qid in capsys.readouterr().err


def test_successful_push_is_also_audit_logged(tmp_repo, monkeypatch):
    _mock_urlopen(monkeypatch, status=202)
    _, qid = _enqueue(tmp_repo)

    review_hitl(qid, tmp_repo, "approve")

    pushes = _events(tmp_repo, "hitl_push")
    assert len(pushes) == 1 and pushes[0]["outcome"] == "started"


def test_a_broken_audit_sink_never_breaks_the_push(tmp_repo, monkeypatch):
    """Same never-raise contract as _emit_review: an unwritable audit stream must
    not turn a successful dispatch into an exception."""
    _mock_urlopen(monkeypatch, status=202)
    (tmp_repo / "state" / "autonomic_events.jsonl").mkdir()   # writes will EISDIR
    _, qid = _enqueue(tmp_repo)

    assert review_hitl(qid, tmp_repo, "approve") is True
    assert _item(tmp_repo, qid)["status"] == "dispatched"


# ── approval TTL + re-notification ────────────────────────────────────────────

def _write_approved(tmp_repo, approved_at: str | None, **extra) -> None:
    item = {
        "id": "hitl-stale", "source": "reflex", "status": "approved",
        "skill": "wiki", "command": "/wiki", "pillar": "arts",
        "metric_id": "metric:arts:Wiki_Health_Score", "backlog_id": None,
        "approved_at": approved_at, "executing_at": None, "completed_at": None,
    }
    item.update(extra)
    (tmp_repo / "state" / "hitl_queue.json").write_text(
        json.dumps({"schema_version": 1, "items": [item]}), encoding="utf-8")


def test_stale_approval_is_resurfaced_after_the_ttl(tmp_repo):
    """hitl-c998eca2 sat `approved` for six days and nothing anywhere said so —
    hitl_alerts.py reports `pending` and recently-`expired` only."""
    _write_approved(tmp_repo, (NOW - timedelta(hours=30)).isoformat())

    assert reconcile_stale_approved(tmp_repo, now=NOW) == 1

    stale = _events(tmp_repo, "hitl_approval_stale")
    assert len(stale) == 1 and stale[0]["queue_id"] == "hitl-stale"


def test_resurfacing_is_non_destructive(tmp_repo):
    """The approval must stay consumable — re-notification is not expiry."""
    _write_approved(tmp_repo, (NOW - timedelta(hours=30)).isoformat())
    reconcile_stale_approved(tmp_repo, now=NOW)

    item = _item(tmp_repo, "hitl-stale")
    assert item["status"] == "approved"
    assert item["approval_renotify_count"] == 1
    assert _consume_approval(_work_item(), tmp_repo) == "hitl-stale"


def test_fresh_approval_is_not_resurfaced(tmp_repo):
    _write_approved(tmp_repo, (NOW - timedelta(hours=1)).isoformat())
    assert reconcile_stale_approved(tmp_repo, now=NOW) == 0
    assert _events(tmp_repo, "hitl_approval_stale") == []


def test_renotification_is_at_most_once_per_window(tmp_repo):
    _write_approved(tmp_repo, (NOW - timedelta(hours=30)).isoformat())

    assert reconcile_stale_approved(tmp_repo, now=NOW) == 1
    assert reconcile_stale_approved(tmp_repo, now=NOW + timedelta(hours=1)) == 0
    assert reconcile_stale_approved(tmp_repo, now=NOW + timedelta(hours=25)) == 1
    assert _item(tmp_repo, "hitl-stale")["approval_renotify_count"] == 2


def test_approved_row_without_a_timestamp_is_treated_as_stale(tmp_repo):
    """A missing/garbled approved_at is exactly the rot case — it must not hide."""
    _write_approved(tmp_repo, None)
    assert reconcile_stale_approved(tmp_repo, now=NOW) == 1


@pytest.mark.parametrize("status", ["pending", "executing", "dispatched", "rejected", "done"])
def test_only_approved_rows_are_resurfaced(tmp_repo, status):
    _write_approved(tmp_repo, (NOW - timedelta(days=9)).isoformat(), status=status)
    assert reconcile_stale_approved(tmp_repo, now=NOW) == 0


def test_ttl_window_is_configurable(tmp_repo, monkeypatch):
    monkeypatch.setenv("BUSHIDO_APPROVAL_TTL_HOURS", "2")
    _write_approved(tmp_repo, (NOW - timedelta(hours=3)).isoformat())
    assert reconcile_stale_approved(tmp_repo, now=NOW) == 1


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
def test_malformed_ttl_falls_back_rather_than_disabling_the_check(monkeypatch, bad):
    monkeypatch.setenv("BUSHIDO_APPROVAL_TTL_HOURS", bad)
    assert be._approval_ttl_hours() == be._APPROVAL_TTL_DEFAULT_HOURS


def test_decide_resurfaces_a_stale_approval_before_consuming_it(tmp_repo):
    """Wiring check: the TTL pass runs on the same reconcile hop as the lease pass."""
    _write_approved(tmp_repo, (NOW - timedelta(days=6)).isoformat())

    tier, consumed = decide(_work_item(), tmp_repo)

    assert (tier, consumed) == (Tier.AUTO, "hitl-stale")
    assert len(_events(tmp_repo, "hitl_approval_stale")) == 1
