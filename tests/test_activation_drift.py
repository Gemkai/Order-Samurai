"""activation-drift doctor family (coverage review R5): landed must mean live.

Every external call is injected; nothing here reads the live process table or a real
remote. Each probe has a "cannot measure" path asserted to be WARN, never OK.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import activation_drift as ad  # noqa: E402

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
LSTART = "Sat Aug 29 04:45:27 2026"   # ps lstart format, local time


def _touch(p: Path, when: datetime, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


def _runner(table: dict[tuple, tuple[int, str]]):
    """A fake subprocess runner keyed on the argv prefix."""
    def run(argv):
        for key, val in table.items():
            if tuple(argv[:len(key)]) == key:
                return val
        return (1, "")
    return run


def _start_local() -> datetime:
    return datetime.strptime(LSTART, "%a %b %d %H:%M:%S %Y").astimezone()


# ── services ─────────────────────────────────────────────────────────────────

def test_source_newer_than_process_is_a_warn_and_names_the_file(tmp_path):
    script = _touch(tmp_path / "Governance" / "dashboard-ui" / "os_dashboard_server.py", _start_local() - timedelta(days=3))
    _touch(tmp_path / "Governance" / "dashboard-ui" / "src" / "App.tsx", _start_local() + timedelta(hours=5))
    run = _runner({
        ("lsof", "-nP", "-iTCP:4322"): (0, "p1060\n"),
        ("ps",): (0, f"{LSTART} /usr/bin/python3 {script}\n"),
        ("lsof", "-p", "1060"): (0, "p1060\nn/\n"),
    })
    rows = ad.service_checks({4322: "order-samurai-dashboard"}, tmp_path, run, NOW)
    assert rows[0]["status"] == "WARN"
    assert rows[0]["label"] == "activation-drift.order-samurai-dashboard.source-newer"
    assert "App.tsx is 5.0h newer" in rows[0]["detail"]


def test_source_older_than_process_is_ok(tmp_path):
    script = _touch(tmp_path / "Execution" / "agent_hq" / "serve.py", _start_local() - timedelta(days=1))
    run = _runner({
        ("lsof", "-nP", "-iTCP:4477"): (0, "p68653\n"),
        ("ps",): (0, f"{LSTART} python3 {script}\n"),
        ("lsof", "-p", "68653"): (0, f"n{tmp_path / 'Execution' / 'agent_hq'}\n"),
    })
    rows = ad.service_checks({4477: "agent-hq"}, tmp_path, run, NOW)
    assert [r["status"] for r in rows] == ["OK"]


def test_relative_argv_resolves_through_cwd(tmp_path):
    """tsx runs `src/server.ts` relative to WorkingDirectory — the 3001 shape."""
    api = tmp_path / "Governance" / "api"
    _touch(api / "src" / "server.ts", _start_local() + timedelta(hours=2))
    run = _runner({
        ("lsof", "-nP", "-iTCP:3001"): (0, "p1493\n"),
        ("ps",): (0, f"{LSTART} node {api}/node_modules/tsx/dist/cli.mjs src/server.ts\n"),
        ("lsof", "-p", "1493"): (0, f"n{api}\n"),
    })
    rows = ad.service_checks({3001: "order-samurai-api"}, tmp_path, run, NOW)
    assert rows[0]["label"].endswith("source-newer")
    assert "server.ts" in rows[0]["detail"]


def test_absolute_script_path_with_spaces_is_reconstructed(tmp_path):
    root = tmp_path / "Agentica OS"
    script = _touch(root / "Governance" / "Order Samurai" / "api.py",
                    _start_local() + timedelta(hours=2))
    run = _runner({
        ("lsof", "-nP", "-iTCP:4322"): (0, "p1060\n"),
        ("ps",): (0, f"{LSTART} /usr/bin/python3 {script} --serve\n"),
        ("lsof", "-p", "1060"): (0, "n/\n"),
    })
    rows = ad.service_checks({4322: "order-samurai-dashboard"}, root, run, NOW)
    assert rows[0]["label"].endswith("source-newer")
    assert "api.py" in rows[0]["detail"]


def test_dist_older_than_src_is_its_own_warn(tmp_path):
    ui = tmp_path / "Governance" / "dashboard-ui"
    script = _touch(ui / "os_dashboard_server.py", _start_local() - timedelta(days=3))
    _touch(ui / "src" / "App.tsx", _start_local() - timedelta(days=1))
    _touch(ui / "dist" / "index.html", _start_local() - timedelta(days=2))
    run = _runner({
        ("lsof", "-nP", "-iTCP:4322"): (0, "p1\n"),
        ("ps",): (0, f"{LSTART} python3 {script}\n"),
        ("lsof", "-p", "1"): (0, "n/\n"),
    })
    rows = ad.service_checks({4322: "order-samurai-dashboard"}, tmp_path, run, NOW)
    # Every source file predates the process, so the process row is OK — the stale dist
    # is a separate, independent finding (a static server does not need a restart, it
    # needs a rebuild).
    assert [(r["status"], r["label"]) for r in rows] == [
        ("OK", "activation-drift.order-samurai-dashboard"),
        ("WARN", "activation-drift.order-samurai-dashboard.dist-stale"),
    ]
    assert "rebuild dist" in rows[-1]["detail"] and "newer than dist by 24.0h" in rows[-1]["detail"]


@pytest.mark.parametrize("lsof_result,expected", [
    ((1, ""), "nothing is listening"),
    ((127, ""), "lsof not found"),
    ((1, "__error__ Permission denied"), "lsof failed"),
])
def test_unmeasurable_service_is_a_warn_never_an_ok(tmp_path, lsof_result, expected):
    rows = ad.service_checks({4323: "morning-joe-dashboard"}, tmp_path,
                             _runner({("lsof", "-nP", "-iTCP:4323"): lsof_result}), NOW)
    assert len(rows) == 1
    assert rows[0]["status"] == "WARN"
    assert rows[0]["label"] == "activation-drift.morning-joe-dashboard"
    assert expected in rows[0]["detail"]


def test_argv_outside_the_repo_and_cwd_root_cannot_measure(tmp_path):
    run = _runner({
        ("lsof", "-nP", "-iTCP:4321"): (0, "p7\n"),
        ("ps",): (0, f"{LSTART} /opt/other/server.py\n"),
        ("lsof", "-p", "7"): (0, "n/\n"),
    })
    rows = ad.service_checks({4321: "brain3-dashboard"}, tmp_path, run, NOW)
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]


# ── sub-bundles ───────────────────────────────────────────────────────────────

def _gitmodules(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".gitmodules").write_text(
        '[submodule "sub-bundles/claude"]\n\tpath = sub-bundles/claude\n\turl = x\n', encoding="utf-8")


def test_gitlink_drift_and_behind_origin_are_warns(tmp_path):
    _gitmodules(tmp_path)
    sub = str(tmp_path / "sub-bundles" / "claude")
    run = _runner({
        ("git", "-C", str(tmp_path), "ls-tree"): (0, "160000 commit 7099a84d201c849b2f7b13fc7d0a95f0cb0f0c30\tsub-bundles/claude\n"),
        ("git", "-C", sub, "rev-parse"): (0, "59b72701cdfa000000000000000000000000abcd\n"),
        ("git", "-C", sub, "rev-list", "--count", "HEAD..origin/main"): (0, "3\n"),
    })
    rows = ad.submodule_checks(tmp_path, run)
    assert [r["label"] for r in rows] == ["activation-drift.sub-bundles.claude.gitlink-drift",
                                         "activation-drift.sub-bundles.claude.behind-origin"]
    assert "pins 7099a84d201c, checkout is at 59b72701cdfa" in rows[0]["detail"]
    assert "3 commit(s) behind origin/main" in rows[1]["detail"]


def test_submodule_in_sync_is_ok(tmp_path):
    _gitmodules(tmp_path)
    sub = str(tmp_path / "sub-bundles" / "claude")
    sha = "59b72701cdfa000000000000000000000000abcd"
    run = _runner({
        ("git", "-C", str(tmp_path), "ls-tree"): (0, f"160000 commit {sha}\tsub-bundles/claude\n"),
        ("git", "-C", sub, "rev-parse"): (0, f"{sha}\n"),
        ("git", "-C", sub, "rev-list", "--count", "HEAD..origin/main"): (0, "0\n"),
    })
    assert [r["status"] for r in ad.submodule_checks(tmp_path, run)] == ["OK"]


def test_no_gitmodules_cannot_measure(tmp_path):
    rows = ad.submodule_checks(tmp_path, _runner({}))
    assert rows[0]["status"] == "WARN" and "cannot measure" in rows[0]["detail"]


# ── main checkout ─────────────────────────────────────────────────────────────

def test_main_checkout_far_behind_with_stale_fetch(tmp_path):
    (tmp_path / ".git").mkdir()
    fetch = tmp_path / ".git" / "FETCH_HEAD"
    fetch.write_text("x")
    old = (NOW - timedelta(hours=40)).timestamp()
    os.utime(fetch, (old, old))
    run = _runner({("git", "-C", str(tmp_path), "rev-list", "--count", "HEAD..origin/work"): (0, "123\n")})
    rows = ad.main_checkout_checks(tmp_path, run, NOW)
    assert rows[0]["status"] == "WARN" and "123 commits behind origin/work (a floor" in rows[0]["detail"]
    assert rows[1]["status"] == "WARN" and "last fetch 40h ago" in rows[1]["detail"]


def test_main_checkout_current_is_ok(tmp_path):
    (tmp_path / ".git").mkdir()
    fetch = tmp_path / ".git" / "FETCH_HEAD"
    fetch.write_text("x")
    recent = (NOW - timedelta(hours=1)).timestamp()
    os.utime(fetch, (recent, recent))
    run = _runner({("git", "-C", str(tmp_path), "rev-list", "--count", "HEAD..origin/work"): (0, "2\n")})
    assert [r["status"] for r in ad.main_checkout_checks(tmp_path, run, NOW)] == ["OK", "OK"]


def test_main_checkout_not_a_repo_cannot_measure(tmp_path):
    rows = ad.main_checkout_checks(tmp_path, _runner({}), NOW)
    assert rows == [{"status": "WARN", "label": "activation-drift.main-checkout",
                     "detail": f"{tmp_path} is not a git checkout; cannot measure"}]


def test_run_checks_composes_all_three_probes(tmp_path):
    (tmp_path / ".git").mkdir()
    rows = ad.run_checks(services={}, repo_root=tmp_path, main_checkout=tmp_path, run=_runner({}), now=NOW)
    labels = {r["label"] for r in rows}
    assert "activation-drift.sub-bundles" in labels
    assert "activation-drift.main-checkout.behind-origin" in labels
    assert all(r["status"] == "WARN" for r in rows)   # nothing measurable in an empty tree → all WARN


def test_newest_mtime_skips_build_dirs_and_reports_truncation(tmp_path):
    _touch(tmp_path / "a.py", NOW - timedelta(days=2))
    _touch(tmp_path / "node_modules" / "x.js", NOW)
    _touch(tmp_path / "dist" / "y.js", NOW)
    newest, path, truncated = ad.newest_mtime(tmp_path)
    assert path == tmp_path / "a.py" and truncated is False
    assert newest == datetime.fromtimestamp((NOW - timedelta(days=2)).timestamp(), timezone.utc)
    for i in range(5):
        _touch(tmp_path / f"f{i}.py", NOW - timedelta(days=1))
    _, _, truncated = ad.newest_mtime(tmp_path, max_files=3)
    assert truncated is True
