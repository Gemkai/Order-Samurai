from agentica_core import render

PAYLOAD = {
    "timestamp": "2026-06-01T00:00:00Z",
    "platforms": ["claude", "antigravity"],
    "record_counts": {"claude": 1, "antigravity": 2295},
    "pillars": {
        "bow": {"Activity": {"Complexity_Weighted_Throughput": {"val": "2296", "tier": "DERIVED", "is_percent": False,
                                             "is_count": True, "is_simulated": False}}},
        "sword": {"Vulnerability": {"Vulnerability_MTTR": {"val": "—", "tier": "SIMULATED", "is_percent": False,
                                                   "is_count": True, "is_simulated": True}}},
        "brush": {}, "arts": {},
    },
    "by_platform": {
        "claude": {"bow": {"Activity": {"Complexity_Weighted_Throughput": {"val": "1", "tier": "DERIVED", "is_percent": False,
                                                       "is_count": True, "is_simulated": False}}},
                   "sword": {}, "brush": {}, "arts": {}},
        "antigravity": {"bow": {}, "sword": {}, "brush": {}, "arts": {}},
    },
}


PAYLOAD_7 = {**PAYLOAD, "window": {"days": 7, "records": 100}}
PAYLOAD_TOTAL = {**PAYLOAD, "window": {"days": 36500, "records": 9999}}
MULTI_PAYLOADS = {"week": PAYLOAD_7, "month": PAYLOAD, "total": PAYLOAD_TOTAL}


def test_single_payload_has_no_javascript():
    html = render.render_html(PAYLOAD)
    assert "<!doctype html>" in html
    assert "<script" not in html  # single-payload core view must not depend on JS


def test_multi_window_render_has_three_grids():
    html = render.render_html(MULTI_PAYLOADS)
    assert html.count('class="grid main-grid"') == 3


def test_multi_window_render_contains_segmented_control():
    html = render.render_html(MULTI_PAYLOADS)
    assert 'class="wc-btn"' in html or "wc-btn" in html
    assert "Week" in html and "Month" in html and "Total" in html


def test_multi_window_render_includes_toggle_js():
    html = render.render_html(MULTI_PAYLOADS)
    assert "<script>" in html
    assert "setWin" in html or "data-w=" in html


def test_multi_window_default_active_window_is_month():
    html = render.render_html(MULTI_PAYLOADS)
    assert 'data-w="month"' in html


def test_write_dashboard_accepts_multi_window(tmp_path):
    out = tmp_path / "dashboard.html"
    assert render.write_dashboard(MULTI_PAYLOADS, path=out) == out
    content = out.read_text(encoding="utf-8")
    assert content.startswith("<!doctype html>")
    assert 'data-window="week"' in content
    assert 'data-window="month"' in content
    assert 'data-window="total"' in content


def test_pillars_and_counts_present():
    html = render.render_html(PAYLOAD)
    for name in ("Bow", "Sword", "Brush", "Arts"):
        assert name in html
    assert "1 live" in html and "1 simulated" in html


def test_live_metric_value_rendered_in_card():
    html = render.render_html(PAYLOAD)
    assert "2296" in html               # the live Throughput value
    assert "Complexity Weighted Throughput" in html


def test_simulated_metric_marked():
    html = render.render_html(PAYLOAD)
    assert "SIMULATED" in html
    assert "Vulnerability MTTR" in html          # underscores replaced with spaces


def test_per_platform_details_present():
    html = render.render_html(PAYLOAD)
    assert "platform: claude" in html and "platform: antigravity" in html
    assert "2295" in html               # record count in summary


def test_write_dashboard_roundtrip(tmp_path):
    out = tmp_path / "dashboard.html"
    assert render.write_dashboard(PAYLOAD, path=out) == out
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_handles_empty_pillars():
    minimal = {"timestamp": "", "platforms": [], "record_counts": {},
               "pillars": {"bow": {}, "sword": {}, "brush": {}, "arts": {}}, "by_platform": {}}
    html = render.render_html(minimal)
    assert "0 live" in html and "0 simulated" in html
    assert "no metrics" in html
