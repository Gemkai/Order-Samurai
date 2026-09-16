"""Open-PR health: WARN on a stale/blocked open PR, WARN-unmeasured when it can't be checked.

Order Samurai's local automation (doctor.py, the nightly launchd jobs) had zero
visibility into GitHub PR state -- a PR could sit open, CI-red, or conflicted
for days and nothing in the local governance loop would ever surface it (found
2026-09-04: a cloud session hand-babysat PR #254 for ~2 days via its own
send_later check-in loop, entirely invisible to this machine's automation
because nothing here polls GitHub). This is a thin, non-gating bridge: it
shells out to the `gh` CLI, if present and authenticated, and reports per-PR
problems as WARN rows a nightly report or doctor run will surface.

Deliberately narrow, and deliberately spectator (gating=False in doctor's
family registry): this does not replace a live drive-to-green loop a Claude
Code session runs against a subscribed PR (see docs/solutions/workflow-patterns/
babysitting-a-pr-check-in-loop-2026-09-04.md for that pattern) -- it is an
offline-friendly "is anything stuck" signal for between sessions, checked once
per doctor run, not an event stream. If `gh` is missing, not authenticated, or
the repo can't be resolved, this reports WARN "cannot verify" -- never a
synthetic OK or FAIL. Not ERROR: doctor counts ERROR for every family and exits
2 on it, so an ERROR here made this explicitly non-gating spectator gate every
doctor run on a machine without `gh` -- including the public export's own CI
gate, and every fresh clone (2026-09-06). The "could not measure" convention
its spectator siblings use (scheduled-run-outcomes.unmeasured on a missing
launchctl, incident-coverage.unmeasured) is WARN, and this now matches it.

The repo comes from GITHUB_REPOSITORY when set (GitHub Actions sets it on every
run) and falls back to this repo's own coordinates -- which the public exporter
scrubs to a placeholder, so outside CI the exported copy reports "cannot verify"
rather than querying a repo it does not belong to.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

_LABEL = "open-pr-health"
_REPO = "example-org/Agentica-OS"


def _default_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or _REPO
# A PR open this long while still blocked is worth a human glance; short of that,
# a fresh conflict/CI-red is normal mid-review noise, not yet a WARN.
_STALE_DAYS = 3.0
_GH_JSON_FIELDS = "number,title,updatedAt,mergeable,statusCheckRollup"

RunnerFn = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)


def _pr_problems(pr: dict, now: datetime) -> list[str]:
    problems: list[str] = []
    if pr.get("mergeable") == "CONFLICTING":
        problems.append("merge conflict")
    rollup = pr.get("statusCheckRollup") or []
    failing = sorted({
        (c.get("name") or c.get("context") or "unnamed check")
        for c in rollup
        if str(c.get("conclusion", "")).upper() == "FAILURE"
        or str(c.get("state", "")).upper() == "FAILURE"
    })
    if failing:
        problems.append(f"CI red: {', '.join(failing)[:160]}")
    return problems


def run_checks(runner: RunnerFn | None = None, repo: str | None = None,
               stale_days: float = _STALE_DAYS,
               now: datetime | None = None,
               which: Callable[[str], str | None] = shutil.which) -> list[dict[str, str]]:
    repo = repo or _default_repo()
    if which("gh") is None:
        return [_make_result("WARN", _LABEL,
                             "gh CLI not installed -- cannot verify open PR health "
                             "(install: https://cli.github.com)")]
    run = runner or _default_runner
    try:
        proc = run(["gh", "pr", "list", "--repo", repo, "--state", "open",
                    "--json", _GH_JSON_FIELDS])
    except Exception as exc:  # noqa: BLE001 -- any failure to launch is "can't verify"
        return [_make_result("WARN", _LABEL,
                             f"gh pr list failed to run ({exc.__class__.__name__}) "
                             f"-- cannot verify open PR health")]
    if proc.returncode != 0:
        return [_make_result("WARN", _LABEL,
                             f"gh pr list exited {proc.returncode} -- likely not "
                             f"authenticated (try 'gh auth login') -- cannot verify "
                             f"open PR health: {proc.stderr.strip()[:200]}")]
    try:
        prs = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return [_make_result("WARN", _LABEL,
                             "gh pr list returned unparseable JSON -- cannot verify "
                             "open PR health")]
    if not prs:
        return [_make_result("OK", _LABEL, "no open PRs")]

    now = now or datetime.now(timezone.utc)
    warn_rows: list[dict[str, str]] = []
    for pr in prs:
        problems = _pr_problems(pr, now)
        if not problems:
            continue
        try:
            updated = datetime.fromisoformat(str(pr.get("updatedAt", "")).replace("Z", "+00:00"))
            age_days: float | None = (now - updated).total_seconds() / 86400
            age_note = f"{age_days:.1f}d"
        except (ValueError, TypeError):
            # A missing/malformed updatedAt must not make a blocked PR vanish from the
            # report: defaulting age to 0.0 here (pre-fix) is always < stale_days, so the
            # PR silently `continue`d past -- the same "never a synthetic OK" doctrine
            # this file's docstring states for the gh-unavailable path applies
            # per-PR too. Age unknown -> always surface the problem instead of guessing.
            age_days = None
            age_note = "age unknown"
        if age_days is not None and age_days < stale_days:
            continue
        number = pr.get("number")
        title = pr.get("title", "")
        warn_rows.append(_make_result(
            "WARN", _LABEL,
            f"PR #{number} \"{title}\" open {age_note} with unresolved "
            f"issue(s): {'; '.join(problems)} -- "
            f"https://github.com/{repo}/pull/{number}"))
    if not warn_rows:
        return [_make_result("OK", _LABEL,
                             f"{len(prs)} open PR(s), none stale+blocked "
                             f"(threshold: {stale_days:g}d)")]
    return warn_rows


def main() -> int:
    results = run_checks()
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    _, exit_code = summarize(results)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
