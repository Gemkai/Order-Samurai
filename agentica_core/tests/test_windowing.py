from datetime import datetime, timezone, timedelta
from agentica_core import aggregate as agg


def test_within_days():
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert agg._within_days(now, 30) is True
    assert agg._within_days(old, 30) is False
    assert agg._within_days("garbage", 30) is False


def test_aggregate_exposes_window_and_lifetime():
    p = agg.aggregate(window_days=30)
    assert "window" in p and p["window"]["days"] == 30
    assert "category_scores" in p and "category_scores_lifetime" in p
    # windowed record count never exceeds lifetime total
    assert p["window"]["records"] <= sum(p["record_counts"].values())
