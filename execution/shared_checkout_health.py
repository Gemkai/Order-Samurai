#!/usr/bin/env python3
"""shared_checkout_health — the multi-writer main checkout as a doctor family (R2.1, 2026-09-02).

Sixty-six incident rows in the coverage review (gap class G1 "shared git checkout") had no
detector: a whole-tree `git stash` that destroyed four HITL rows (08-14), a 120-ahead /
25-behind divergence across five sessions (08-31), eighteen stranded branches nobody had
claimed (08-15), commits landing on a peer's branch between check and commit. Only the
09-01 pre-commit hook enforces anything, and it is a git hook, not Order Samurai.

Read-only over one repository (`AGENTICA_MAIN_CHECKOUT`, default ~/AgenticaOS). Local refs
only — doctor never fetches, so "behind" is reported by the activation-drift family, which
also reports how stale the last fetch is. Every probe has a "cannot measure" WARN.

  unpushed        HEAD ahead of `origin/work` — commits that exist on this machine only.
  stranded        a local branch older than `stranded_hours` with commits whose CONTENT is
                  not in origin/work (`git cherry`, patch-id based — the squash-merge blind
                  spot `merge-base --is-ancestor` has), checked out in no worktree, and
                  with no open PR. The PR lookup goes through `gh`; when gh is unavailable
                  the branch is still reported, with "PR status unmeasured" — never
                  silently promoted to "no PR" (Sol 5.6 review, finding 11).
  stash           entries older than `stash_hours`: a stash is a place work goes to die.
  state-dirty     FAIL — a git-TRACKED file under Order Samurai's state/ is modified in the
                  working tree: a machine writer is mutating tracked truth (v2 M5 / R2.1).
  artifacts-dirty WARN — same for artifacts/ (tracked scorer output; D7 pending).
  materialized    WARN per `MM` path: index and working tree both differ from HEAD, the
                  signature of a file brought forward by hand (the R0 precedent) that the
                  next pull will fight over.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], tuple[int, str]]

LABEL = "shared-checkout-health"
SUBPROCESS_TIMEOUT = 20
GH_LOOKUP_CAP = 15
STATE_DIR = "Governance/Order Samurai/state"
ARTIFACTS_DIR = "Governance/Order Samurai/artifacts"


def default_runner(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except FileNotFoundError:
        return (127, "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (1, f"__error__ {exc}")
    return (proc.returncode, proc.stdout or "")


def _row(status: str, label: str, detail: str) -> dict:
    return {"status": status, "label": f"{LABEL}.{label}", "detail": detail}


def _git(run: Runner, repo: Path, *args: str) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args])


def unpushed_checks(repo: Path, run: Runner, upstream: str) -> list[dict]:
    rc, out = _git(run, repo, "rev-list", "--count", f"{upstream}..HEAD")
    if rc != 0 or not out.strip().isdigit():
        return [_row("WARN", "unpushed", f"cannot count {upstream}..HEAD; cannot measure")]
    ahead = int(out.strip())
    if ahead:
        return [_row("WARN", "unpushed", f"HEAD is {ahead} commit(s) ahead of {upstream} — work that "
                                        f"exists on this machine only (push, or it is one bad "
                                        f"checkout away from gone)")]
    return [_row("OK", "unpushed", f"HEAD has nothing {upstream} lacks")]


def worktree_branches(repo: Path, run: Runner) -> set[str] | None:
    rc, out = _git(run, repo, "worktree", "list", "--porcelain")
    if rc != 0:
        return None
    return {ln.split("refs/heads/", 1)[1].strip() for ln in out.splitlines()
            if ln.startswith("branch refs/heads/")}


def open_pr_for(branch: str, run: Runner) -> bool | None:
    """True/False when gh answered; None when it could not (not installed, no auth, timeout)."""
    rc, out = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--limit", "1"])
    if rc != 0 or out.startswith("__error__"):
        return None
    return out.strip() not in ("", "[]")


def stranded_branch_checks(repo: Path, run: Runner, now: datetime, upstream: str,
                           stranded_hours: float = 48.0, gh_cap: int = GH_LOOKUP_CAP) -> list[dict]:
    rc, out = _git(run, repo, "for-each-ref", "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads")
    if rc != 0:
        return [_row("WARN", "stranded", "cannot list local branches; cannot measure")]
    checked_out = worktree_branches(repo, run)
    if checked_out is None:
        return [_row("WARN", "stranded", "cannot list worktrees; cannot measure")]
    upstream_branch = upstream.split("/", 1)[-1]
    rows: list[dict] = []
    gh_calls = 0
    gh_dead = False
    candidates = 0
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, ts = line.split("\t", 1)
        if name == upstream_branch or name in checked_out or not ts.strip().isdigit():
            continue
        age_h = (now - datetime.fromtimestamp(int(ts), timezone.utc)).total_seconds() / 3600.0
        if age_h < stranded_hours:
            continue
        rc2, cherry = _git(run, repo, "cherry", upstream, name)
        if rc2 != 0:
            rows.append(_row("WARN", f"stranded.{name}", f"cannot compare with {upstream} (git cherry failed)"))
            continue
        unmerged = [ln for ln in cherry.splitlines() if ln.startswith("+")]
        if not unmerged:
            continue
        candidates += 1
        pr: bool | None
        if gh_dead or gh_calls >= gh_cap:
            pr = None
        else:
            gh_calls += 1
            pr = open_pr_for(name, run)
            if pr is None:
                gh_dead = True
        if pr is True:
            continue
        pr_note = "no open PR" if pr is False else "PR status unmeasured (gh unavailable or lookup cap reached)"
        rows.append(_row("WARN", f"stranded.{name}",
                         f"{len(unmerged)} commit(s) not in {upstream} by content, no worktree, "
                         f"{age_h / 24:.0f}d old, {pr_note} — claim it (worktree + PR) or delete it"))
    if not rows:
        rows.append(_row("OK", "stranded", f"no stranded branches ({candidates} candidate(s) examined)"))
    return rows


def stash_checks(repo: Path, run: Runner, now: datetime, stash_hours: float = 24.0) -> list[dict]:
    rc, out = _git(run, repo, "stash", "list", "--format=%ct%x09%gd%x09%gs")
    if rc != 0:
        return [_row("WARN", "stash", "cannot list stashes; cannot measure")]
    rows: list[dict] = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        age_h = (now - datetime.fromtimestamp(int(parts[0]), timezone.utc)).total_seconds() / 3600.0
        if age_h > stash_hours:
            rows.append(_row("WARN", f"stash.{parts[1]}",
                             f"{age_h / 24:.0f}d old: {parts[2][:80]} — apply it somewhere or drop it; "
                             f"a stash on the shared checkout is where four HITL rows died on 08-14"))
    if not rows:
        rows.append(_row("OK", "stash", "no stash entries older than a day"))
    return rows


def _watched(paths: list[str]) -> tuple[list[str], list[str]]:
    return ([p for p in paths if p.startswith(STATE_DIR + "/")],
            [p for p in paths if p.startswith(ARTIFACTS_DIR + "/")])


def dirty_tracked_checks(repo: Path, run: Runner) -> list[dict]:
    rc, out = _git(run, repo, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    if rc != 0:
        return [_row("WARN", "state-dirty", "git status failed; cannot measure")]
    rows: list[dict] = []
    materialized: list[str] = []
    fields = out.split("\0")
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(field) < 4:
            continue
        code, dest = field[:2], field[3:]
        source: str | None = None
        if code[0] in "RC" and index < len(fields):
            # In porcelain -z the DESTINATION is in the status record and the source
            # follows as a second NUL-delimited path (no ambiguous "old -> new" text).
            source = fields[index]
            index += 1
        if code == "MM":
            materialized.append(dest)
        if code[1] == "M":
            # The working-tree letter describes the destination alone. After a staged
            # rename the source path no longer exists on disk, so it cannot be what a
            # background writer modified — attributing state-dirty to it (`RM` out of
            # state/) accuses a vanished file. The rename itself is still reported below.
            state_paths, artifact_paths = _watched([dest])
            for state_path in state_paths:
                rows.append(_row("FAIL", "state-dirty",
                                 f"{state_path} is git-tracked and modified in the working tree — a machine "
                                 f"writer is mutating tracked truth; untrack it (v2 M5.1) or stop the writer"))
            for artifact_path in artifact_paths:
                rows.append(_row("WARN", "artifacts-dirty",
                                 f"{artifact_path} (tracked scorer output) modified — decision D7 pending"))
            staged = [source] if source else []
        else:
            staged = [dest] + ([source] if source else [])
        state_paths, artifact_paths = _watched(staged)
        if state_paths or artifact_paths:
            affected = ", ".join(state_paths + artifact_paths)
            rows.append(_row("WARN", "tracked-state",
                             f"{affected} has git status {code!r}; this is staged/deleted/renamed/conflicted "
                             "state, not evidence of a background writer"))
    for path in materialized:
        rows.append(_row("WARN", "materialized",
                         f"{path}: index and working tree both differ from HEAD — a hand-materialized "
                         f"file the next pull will fight over"))
    if not rows:
        rows.append(_row("OK", "state-dirty", f"no tracked file under {STATE_DIR} or {ARTIFACTS_DIR} is modified"))
    return rows


def run_checks(*, main_checkout: Path | None = None, run: Runner | None = None,
               now: datetime | None = None, upstream: str = "origin/work") -> list[dict]:
    run = run or default_runner
    now = now or datetime.now(timezone.utc)
    repo = main_checkout or Path(os.environ.get("AGENTICA_MAIN_CHECKOUT", str(Path.home() / "AgenticaOS")))
    if not (repo / ".git").exists():
        return [_row("WARN", "main-checkout", f"{repo} is not a git checkout; cannot measure")]
    return (unpushed_checks(repo, run, upstream)
            + stranded_branch_checks(repo, run, now, upstream)
            + stash_checks(repo, run, now)
            + dirty_tracked_checks(repo, run))


if __name__ == "__main__":
    for r in run_checks():
        print(f"[{r['status']}] {r['label']}: {r['detail']}")
