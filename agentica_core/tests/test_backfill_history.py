import json

from agentica_core import backfill_history as bh


def test_week_monday_iso():
    # ISO week 2026-W23 Monday = 2026-06-01
    assert bh._week_monday_iso("2026-W23").startswith("2026-06-01")
    assert bh._week_monday_iso("garbage") == "garbage"  # graceful fallback


def test_week_values_flattens_keys():
    recs = [{"timestamp": "2026-06-01T00:00:00+00:00", "project": "x", "model_tier": "FAST",
             "tokens_prompt": 100, "tokens_completion": 50, "tool_calls": 3,
             "status": "success", "session_id": "s1"}]
    vals = bh._week_values(recs)
    assert all("/" in k for k in vals)            # keys are pillar/group/metric
    assert all(isinstance(v, (int, float)) for v in vals.values())


def test_backfill_never_clobbers_history_when_telemetry_yields_nothing(tmp_path, monkeypatch):
    """load_records() returns [] for an unreachable platform or a moved telemetry
    file — a source failure, not evidence that history is empty. Rewriting the
    store from zero rows would wipe every sigma baseline, sparkline and prior-week
    comparison the reducers read back (and refresh_dashboard calls backfill() on
    every --snapshot run)."""
    store = tmp_path / "hist.jsonl"
    existing = ('{"ts": "2026-06-01T00:00:00+00:00", "week": "2026-W23", '
                '"values": {"bow/Activity/Session_Count": 4}}\n')
    store.write_text(existing, encoding="utf-8")

    monkeypatch.setattr(bh.agg, "list_platforms", lambda: [])

    assert bh.backfill(store=store) == 0
    assert store.read_text(encoding="utf-8") == existing


def test_backfill_idempotent(tmp_path):
    store = tmp_path / "hist.jsonl"
    n1 = bh.backfill(store=store)
    rows1 = store.read_text(encoding="utf-8").strip().splitlines()
    n2 = bh.backfill(store=store)
    rows2 = store.read_text(encoding="utf-8").strip().splitlines()
    assert n1 == n2 == len(rows1) == len(rows2)    # stable, no growth
    for ln in rows2:
        row = json.loads(ln)
        assert "week" in row and "values" in row   # weekly-canonical series


# ---------------------------------------------------------------------------
# FIX C — carry live-only metrics across the truncate-and-rebuild (seq 9c)
# ---------------------------------------------------------------------------
# _week_values() can only recompute what telemetry supports. Every live-only
# reducer (scouts, exec-log metrics, security signals) lived exclusively on the
# snapshot rows this rebuild truncates — so each run erased those series and left
# 24 metrics pinned to a single history point forever.

_LIVE_ONLY = "bow/Autonomic/Self_Correction_Rate"
_WEEKLY = "bow/Activity/Session_Count"


def _stub_two_weeks(monkeypatch):
    """Telemetry in ISO weeks 2026-W23 (Mon 06-01) and 2026-W24 (Mon 06-08)."""
    recs = [{"timestamp": "2026-06-02T00:00:00+00:00"},
            {"timestamp": "2026-06-09T00:00:00+00:00"}]
    monkeypatch.setattr(bh.agg, "list_platforms", lambda: ["claude"])
    monkeypatch.setattr(bh.agg, "load_records", lambda _p: recs)
    monkeypatch.setattr(bh, "_week_row", lambda w, wrecs: {
        "ts": bh._week_monday_iso(w), "week": w, "kind": "weekly",
        "values": {_WEEKLY: float(len(wrecs))}})


def _seed_live_row(store, ts: str, value: float) -> None:
    store.write_text(json.dumps({"ts": ts, "kind": "live",
                                 "values": {_LIVE_ONLY: value}}) + "\n", encoding="utf-8")


def _rows(store) -> list[dict]:
    return [json.loads(ln) for ln in store.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_backfill_carries_live_only_metric_onto_the_rebuilt_rows(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    assert bh.backfill(store=store) == 2
    rows = _rows(store)
    assert rows[1]["values"][_LIVE_ONLY] == 2.2          # week starting 06-08 (after the obs)
    assert rows[1]["carried_forward"] == [_LIVE_ONLY]


def test_backfill_does_not_back_date_a_value_onto_earlier_weeks(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    rows = _rows(store)
    assert _LIVE_ONLY not in rows[0]["values"]           # week starting 06-01, before the obs
    assert "carried_forward" not in rows[0]


def test_two_consecutive_backfills_retain_the_live_only_key(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    first = _rows(store)
    bh.backfill(store=store)
    second = _rows(store)

    assert first == second                               # idempotent, not eroding
    assert second[1]["values"][_LIVE_ONLY] == 2.2


# ---------------------------------------------------------------------------
# The prior snapshot's kind:"live" ROW (not just its values) must survive the
# rebuild -- insights._comparable_history_rows() diffs against `kind in (None,
# "live")` rows to compute delta/trend and feed the sigma-anomaly tier. Every
# --snapshot run calls backfill() BEFORE populate_history() (refresh_dashboard.py),
# so if backfill() drops the live row entirely, populate_history() always finds a
# 1-point series and delta/trend/sigma stay stuck at their neutral defaults forever.
# ---------------------------------------------------------------------------

def test_backfill_preserves_the_latest_live_row_across_the_rebuild(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(json.dumps({"ts": "2026-06-10T00:00:00+00:00", "week": "2026-W24",
                                 "kind": "live", "values": {_WEEKLY: 42.0}}) + "\n",
                     encoding="utf-8")

    bh.backfill(store=store)
    rows = _rows(store)
    live = [r for r in rows if r.get("kind") == "live"]
    assert len(live) == 1                                # max_live_rows=1 invariant
    assert live[0]["values"][_WEEKLY] == 42.0


def test_recomputed_weekly_value_wins_over_a_carried_one(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(json.dumps({"ts": "2026-06-05T00:00:00+00:00",
                                 "values": {_WEEKLY: 999.0}}) + "\n", encoding="utf-8")

    bh.backfill(store=store)
    rows = _rows(store)
    assert rows[1]["values"][_WEEKLY] == 1.0             # the real recomputation
    assert "carried_forward" not in rows[1]


def test_carry_forward_kill_switch_restores_the_pure_rebuild(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKFILL_CARRY_FORWARD", "false")
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    _seed_live_row(store, "2026-06-05T00:00:00+00:00", 2.2)

    bh.backfill(store=store)
    rows = _rows(store)
    assert all(_LIVE_ONLY not in r["values"] for r in rows)


def test_backfill_ignores_unparseable_rows_when_carrying_forward(tmp_path, monkeypatch):
    _stub_two_weeks(monkeypatch)
    store = tmp_path / "hist.jsonl"
    store.write_text(
        "not-json\n"
        + json.dumps({"ts": "2026-06-05T00:00:00+00:00", "values": {_LIVE_ONLY: 7.0}}) + "\n",
        encoding="utf-8")

    assert bh.backfill(store=store) == 2
    assert _rows(store)[1]["values"][_LIVE_ONLY] == 7.0
