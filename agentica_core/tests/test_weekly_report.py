"""Tests for weekly_report.py helpers (no filesystem I/O)."""
from agentica_core import weekly_report as wr


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------

def test_fmt_none_returns_dash():
    assert wr._fmt(None) == "—"


def test_fmt_int():
    assert wr._fmt(1234) == "1,234"


def test_fmt_float_no_trailing_zeros():
    assert wr._fmt(12.50) == "12.5"


def test_fmt_float_whole_number():
    assert wr._fmt(12.0) == "12"


def test_fmt_string_passthrough():
    assert wr._fmt("hello") == "hello"


# ---------------------------------------------------------------------------
# _table
# ---------------------------------------------------------------------------

def test_table_renders_header_and_rows():
    t = wr._table([("Error Rate", 0.05), ("Sessions", 10)])
    assert "| Metric | Value |" in t
    assert "Error Rate" in t
    assert "10" in t


# ---------------------------------------------------------------------------
# _pillar_trend_svg
# ---------------------------------------------------------------------------

def test_trend_svg_returns_empty_for_single_week():
    scores = {"2026-W01": {"bow": 90, "sword": 80, "brush": 70, "arts": 60}}
    svg = wr._pillar_trend_svg("2026-W01", scores)
    assert svg == ""


def test_trend_svg_returns_empty_when_week_not_in_scores():
    scores = {"2026-W01": {"bow": 90, "sword": 80, "brush": 70, "arts": 60}}
    svg = wr._pillar_trend_svg("2026-W05", scores)
    assert svg == ""


def test_trend_svg_renders_with_two_weeks():
    scores = {
        "2026-W01": {"bow": 90, "sword": 80, "brush": 70, "arts": 60},
        "2026-W02": {"bow": 88, "sword": 82, "brush": 72, "arts": 55},
    }
    svg = wr._pillar_trend_svg("2026-W02", scores)
    assert svg.startswith("<svg")
    assert "polyline" in svg
    # All 4 pillar colors appear
    for color in wr._PILLAR_HEX.values():
        assert color in svg


def test_trend_svg_windows_to_last_7_weeks():
    # Provide 10 weeks; the SVG should include at most 7.
    scores = {f"2026-W{str(i).zfill(2)}": {"bow": 80, "sword": 80, "brush": 80, "arts": 80}
              for i in range(1, 11)}
    svg = wr._pillar_trend_svg("2026-W10", scores)
    # W01-W03 are outside the 7-week window; W04-W10 are in
    assert "W10" in svg
    # W01 label should not appear (window starts at W04)
    assert ">W01<" not in svg


def test_trend_svg_current_week_in_bold():
    scores = {
        "2026-W05": {"bow": 85, "sword": 75, "brush": 65, "arts": 50},
        "2026-W06": {"bow": 88, "sword": 78, "brush": 68, "arts": 52},
    }
    svg = wr._pillar_trend_svg("2026-W06", scores)
    # The current week label uses font-weight="bold"
    assert 'font-weight="bold"' in svg


# ---------------------------------------------------------------------------
# _nudge_command (in reflexes module — covered there)
# but verify weekly_report imports don't cause circular issues
# ---------------------------------------------------------------------------

def test_import_is_clean():
    import importlib
    mod = importlib.import_module("agentica_core.weekly_report")
    assert hasattr(mod, "generate")
    assert hasattr(mod, "_report_md")
