from agentica_core import state_report


def test_state_report_builds_current_snapshot():
    md = state_report.build_report()
    assert "# Agentica OS Current State" in md
    assert "## Platform Governance" in md
    assert "claude" in md
    assert "codex" in md
    assert "Metrics:" in md
