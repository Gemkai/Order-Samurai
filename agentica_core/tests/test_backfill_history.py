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
