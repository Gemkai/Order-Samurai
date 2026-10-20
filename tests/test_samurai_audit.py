"""`samurai audit`: the customer full audit, run from a web install (~/.samurai/core).

The web installer extracts the zip to ~/.samurai/core and runs bin/samurai by full path,
from whatever directory the buyer is in. These tests run the CLI the same way: by path,
from a directory outside the checkout, with an isolated HOME, so nothing on sys.path
comes from the repository or the test runner.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMURAI = ROOT / "bin" / "samurai"
FIRST_BLOOD = ROOT / "bin" / "first_blood.py"


def _transcript(home: Path) -> None:
    project = home / ".claude" / "projects" / "-Users-buyer-shop"
    project.mkdir(parents=True)
    rows = [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "timestamp": "2026-10-19T12:00:00Z",
         "message": {"model": "claude-sonnet-4-6",
                     "usage": {"input_tokens": 120_000, "output_tokens": 40_000}}},
    ]
    (project / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    return {
        "HOME": str(home),
        "SAMURAI_HOME": str(home / ".samurai"),
        "SAMURAI_REPORTS_DIR": str(tmp_path / "reports"),
        "SAMURAI_NO_PROMPT": "1",
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _run(argv, env, cwd, timeout=240):
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=timeout)


def test_first_blood_runs_by_path_outside_the_checkout(tmp_path, env):
    """bin/first_blood.py must import the bundled agentica_core (a CHILD of the install
    root), not only a sibling of it. From ~/.samurai/core it raised ModuleNotFoundError."""
    _transcript(Path(env["HOME"]))
    result = _run([sys.executable, str(FIRST_BLOOD), "--json"], env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["sessions_scanned"] == 1


def test_audit_writes_a_report_and_index_the_dashboard_can_list(tmp_path, env):
    _transcript(Path(env["HOME"]))
    result = _run([sys.executable, str(SAMURAI), "audit"], env, cwd=tmp_path)

    # Hooks are not installed in this HOME, so doctor reports findings: exit 1, not 2.
    assert result.returncode in (0, 1), result.stdout + result.stderr
    reports = tmp_path / "reports"
    audits = sorted(reports.glob("audit__*.md"))
    assert len(audits) == 1, result.stdout + result.stderr
    body = audits[0].read_text(encoding="utf-8")
    assert body.startswith("# Full Audit")
    assert "## Health checks" in body
    assert "Python Version" in body           # a real doctor check line, not a placeholder
    assert "## Agent spend" in body
    assert "Sessions scanned: **1**" in body  # first_blood read the seeded transcript

    index = json.loads((reports / "index.json").read_text(encoding="utf-8"))
    assert index[0]["file"] == audits[0].name
    assert index[0]["kind"] == "audit"
    assert index[0]["generated"]
    assert "Reports tab" in result.stdout


def test_audit_without_session_logs_says_so(tmp_path, env):
    result = _run([sys.executable, str(SAMURAI), "audit"], env, cwd=tmp_path)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    body = next((tmp_path / "reports").glob("audit__*.md")).read_text(encoding="utf-8")
    assert "No Claude Code session logs found" in body


def test_audit_exits_2_when_the_report_cannot_be_written(tmp_path, env):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    env = {**env, "SAMURAI_REPORTS_DIR": str(blocker / "reports")}
    result = _run([sys.executable, str(SAMURAI), "audit"], env, cwd=tmp_path)
    assert result.returncode == 2, result.stdout + result.stderr


def _load_cli():
    loader = SourceFileLoader("samurai_cli_audit", str(SAMURAI))
    spec = importlib.util.spec_from_loader("samurai_cli_audit", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_default_reports_dir_is_inside_the_install(tmp_path, monkeypatch):
    """The dashboard's public/ dir is what `npm run dev` serves, and it is inside
    ~/.samurai/core -- unlike weekly_report's old parents[2]/Data/reports."""
    cli = _load_cli()
    monkeypatch.delenv("SAMURAI_REPORTS_DIR", raising=False)
    monkeypatch.setenv("SAMURAI_ROOT", str(tmp_path / "core"))
    assert cli.get_paths()["reports"] == tmp_path / "core" / "dashboard-ui" / "public" / "reports"
    monkeypatch.setenv("SAMURAI_REPORTS_DIR", str(tmp_path / "elsewhere"))
    assert cli.get_paths()["reports"] == tmp_path / "elsewhere"


def test_weekly_report_default_dir_is_the_dashboard_public_reports(monkeypatch):
    monkeypatch.delenv("SAMURAI_REPORTS_DIR", raising=False)
    sys.path.insert(0, str(ROOT))
    from agentica_core import weekly_report
    assert weekly_report.reports_dir() == ROOT / "dashboard-ui" / "public" / "reports"


def test_rebuild_index_lists_audits_and_weekly_reports_newest_first(tmp_path):
    sys.path.insert(0, str(ROOT))
    from agentica_core import weekly_report
    (tmp_path / "2026-W42__claude.md").write_text("Records this week: **7**\n", encoding="utf-8")
    old = tmp_path / "audit__20261001T000000Z.md"
    new = tmp_path / "audit__20261019T120000Z.md"
    old.write_text("# Full Audit\n", encoding="utf-8")
    new.write_text("# Full Audit\n", encoding="utf-8")
    (tmp_path / "current-state.md").write_text("not a report\n", encoding="utf-8")
    os.utime(tmp_path / "2026-W42__claude.md", (1_760_000_000, 1_760_000_000))  # 2025-10-09

    index = weekly_report.rebuild_index(tmp_path)

    assert [e["file"] for e in index] == [new.name, old.name, "2026-W42__claude.md"]
    assert index[0]["kind"] == "audit" and index[0]["generated"] == "2026-10-19T12:00:00Z"
    assert index[2] == {**index[2], "kind": "weekly", "week": "2026-W42", "platform": "claude", "records": 7}
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8")) == index


def test_report_cells_cannot_inject_markup(tmp_path):
    """The dashboard passes inline <svg> through verbatim; a project directory name
    must not be able to smuggle one into the audit report."""
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from agentica_core import full_audit
    spend = {"sessions_scanned": 1, "total_cost": 9.0, "cost_per_task": 9.0, "token_spend": 1,
             "token_execution_density": 1, "spend_spike_threshold_usd": 5.0, "note": "n",
             "spend_spikes": [{"project": "<svg onload=alert(1)></svg>", "session_id": "s", "estimated_cost": 9.0}]}
    doctor = {"ok": True, "ran": True, "passed": 1, "total": 1, "error": None,
              "checks": [{"name": "X", "result": "PASS", "detail": "<svg/onload=1>"}]}
    body = full_audit.render(datetime.now(timezone.utc), "0", doctor, spend, tmp_path, 0)
    assert "<svg" not in body


def test_audit_runs_when_the_cli_python_lacks_report_dependencies(tmp_path, env):
    """On a PEP 668 Python, bin/install.sh puts jsonschema & co. in ~/.samurai/venv and the
    CLI runs on bare python3. Found by the clean-HOME install: the audit crashed importing
    aggregate -> jsonschema before writing anything."""
    _transcript(Path(env["HOME"]))
    code = ("import runpy, sys; sys.modules['jsonschema'] = None; "
            f"sys.argv = [{str(SAMURAI)!r}, 'audit']; runpy.run_path({str(SAMURAI)!r}, run_name='__main__')")
    result = _run([sys.executable, "-c", code], env, cwd=tmp_path)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    body = next((tmp_path / "reports").glob("audit__*.md")).read_text(encoding="utf-8")
    assert "Sessions scanned: **1**" in body


def test_report_python_prefers_the_install_venv(tmp_path):
    sys.path.insert(0, str(ROOT))
    from agentica_core import full_audit
    assert full_audit.report_python(tmp_path) == sys.executable
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n", encoding="utf-8")
    venv_py.chmod(0o755)
    assert full_audit.report_python(tmp_path) == str(venv_py)


def test_missing_report_dependencies_name_the_fix(tmp_path):
    """The repo installer does not install report dependencies; say how to add them."""
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from agentica_core import full_audit
    doctor = {"ok": True, "ran": True, "passed": 1, "total": 1, "error": None, "checks": []}
    body = full_audit.render(datetime.now(timezone.utc), "0", doctor, None, tmp_path, 0,
                             spend_error="ModuleNotFoundError: No module named 'jsonschema'", root=ROOT)
    assert "No module named 'jsonschema'" in body
    assert f"bash {ROOT}/bin/install.sh" in body


def test_a_fresh_audit_sorts_first_even_after_weekly_rewrites(tmp_path):
    """weekly_report rewrites every weekly .md during the audit; the audit written after
    them must still be index[0], or Free users see their new audit blurred."""
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from agentica_core import report_index
    now = datetime.now(timezone.utc)
    (tmp_path / "2026-W42__claude.md").write_text("Records this week: **3**\n", encoding="utf-8")
    (tmp_path / f"audit__{now:%Y%m%dT%H%M%SZ}.md").write_text("# Full Audit\n", encoding="utf-8")
    index = report_index.rebuild_index(tmp_path)
    assert index[0]["kind"] == "audit"
