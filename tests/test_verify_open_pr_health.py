"""Tests for the open-PR health check (execution/verify_open_pr_health.py).

The failure class under test: this repo's local automation (doctor.py, the
nightly launchd jobs) had zero visibility into GitHub PR state before this
check existed. The check must WARN "cannot verify" (never a synthetic OK/FAIL,
and not ERROR -- doctor exits 2 on any ERROR, which made this non-gating
spectator gate every machine without `gh`) when `gh` can't answer, stay OK on
a clean or empty PR list, and WARN once per PR that
is both blocked (conflict or CI-red) AND stale past the threshold -- a fresh
conflict on a brand-new PR is normal review noise, not yet a WARN.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import verify_open_pr_health as voph  # noqa: E402

_NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _proc(stdout: str = "[]", returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def _pr(number=1, title="a PR", days_old=0.0, mergeable="MERGEABLE",
        failing_checks: list[str] | None = None):
    updated = (_NOW - timedelta(days=days_old)).isoformat().replace("+00:00", "Z")
    rollup = [{"name": name, "conclusion": "FAILURE"} for name in (failing_checks or [])]
    return {"number": number, "title": title, "updatedAt": updated,
            "mergeable": mergeable, "statusCheckRollup": rollup}


def _which_present(_name):
    return "/usr/bin/gh"


def _which_missing(_name):
    return None


# ── cannot-verify paths (WARN "unmeasured", never a synthetic OK/FAIL) ──────────

def test_gh_not_installed_is_unmeasured_warn():
    results = voph.run_checks(which=_which_missing, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
    assert "not installed" in results[0]["detail"]
    assert "cannot verify" in results[0]["detail"]


def test_gh_launch_failure_is_unmeasured_warn():
    def boom(_args):
        raise FileNotFoundError("no such file")
    results = voph.run_checks(runner=boom, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
    assert "cannot verify" in results[0]["detail"]


def test_gh_nonzero_exit_is_unmeasured_warn():
    def runner(_args):
        return _proc(returncode=1, stderr="not authenticated")
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
    assert "not authenticated" in results[0]["detail"]


def test_unparseable_json_is_unmeasured_warn():
    def runner(_args):
        return _proc(stdout="not json")
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]


# ── repo resolution (the exporter scrubs the literal; CI sets GITHUB_REPOSITORY) ──

def test_github_repository_env_selects_the_repo(monkeypatch):
    seen = {}
    def runner(args):
        seen["args"] = args
        return _proc()
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/elsewhere")
    voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert seen["args"][seen["args"].index("--repo") + 1] == "someone/elsewhere"


def test_explicit_repo_beats_the_env(monkeypatch):
    seen = {}
    def runner(args):
        seen["args"] = args
        return _proc()
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/elsewhere")
    voph.run_checks(runner=runner, which=_which_present, now=_NOW, repo="explicit/repo")
    assert seen["args"][seen["args"].index("--repo") + 1] == "explicit/repo"


def test_default_repo_without_env_is_this_repo(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert voph._default_repo() == voph._REPO


# ── OK paths ─────────────────────────────────────────────────────────────────

def test_no_open_prs_ok():
    def runner(_args):
        return _proc(stdout="[]")
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]
    assert "no open PRs" in results[0]["detail"]


def test_open_prs_all_clean_ok():
    prs = [_pr(number=1, days_old=5.0), _pr(number=2, days_old=0.1)]
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]
    assert "2 open PR" in results[0]["detail"]


def test_blocked_but_fresh_pr_does_not_warn():
    # Conflicted/CI-red on a PR opened minutes ago is ordinary review noise --
    # WARNing on it would make every freshly-pushed PR trigger a false alarm.
    prs = [_pr(number=1, days_old=0.1, mergeable="CONFLICTING")]
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["OK"]


# ── WARN paths ───────────────────────────────────────────────────────────────

def test_stale_conflicted_pr_warns():
    prs = [_pr(number=254, title="chore: backlog cleanup", days_old=2.0,
                mergeable="CONFLICTING")]
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW,
                              stale_days=1.0)
    assert [r["status"] for r in results] == ["WARN"]
    assert "PR #254" in results[0]["detail"]
    assert "merge conflict" in results[0]["detail"]


def test_stale_ci_red_pr_warns_and_names_the_job():
    prs = [_pr(number=254, days_old=2.0,
                failing_checks=["Public export builds and its suite passes"])]
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW,
                              stale_days=1.0)
    assert [r["status"] for r in results] == ["WARN"]
    assert "CI red" in results[0]["detail"]
    assert "Public export builds" in results[0]["detail"]


def test_blocked_pr_with_malformed_updated_at_still_warns():
    # A PR record missing/malformed `updatedAt` must not vanish from the report just
    # because its age can't be computed -- defaulting age to 0.0 (pre-fix) always reads
    # as "not stale" and the blocked PR is silently dropped, producing a synthetic OK
    # for a repo that actually has a stuck, conflicted PR.
    prs = [_pr(number=254, title="chore: backlog cleanup", mergeable="CONFLICTING")]
    prs[0]["updatedAt"] = "not-a-timestamp"
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
    assert "PR #254" in results[0]["detail"]
    assert "merge conflict" in results[0]["detail"]


def test_only_blocked_prs_produce_warn_rows():
    prs = [_pr(number=1, days_old=5.0),  # clean
           _pr(number=2, days_old=5.0, mergeable="CONFLICTING")]  # blocked+stale
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    assert [r["status"] for r in results] == ["WARN"]
    assert "PR #2" in results[0]["detail"]


# ── summarize() contract (never a synthetic FAIL) ───────────────────────────

def test_unmeasured_does_not_fail_the_exit_code():
    results = voph.run_checks(which=_which_missing, now=_NOW)
    _, exit_code = voph.summarize(results)
    assert exit_code == 0  # WARN-unmeasured, never 1 or 2 (no FAIL was ever measured)


def test_warn_does_not_fail_the_exit_code():
    prs = [_pr(number=1, days_old=5.0, mergeable="CONFLICTING")]
    def runner(_args):
        return _proc(stdout=json.dumps(prs))
    results = voph.run_checks(runner=runner, which=_which_present, now=_NOW)
    _, exit_code = voph.summarize(results)
    assert exit_code == 0
