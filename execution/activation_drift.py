#!/usr/bin/env python3
"""activation_drift — "landed means live" as a doctor family (coverage review R5, 2026-09-02).

Three Order Samurai fixes in one quarter (07-14 reflex staging, 08-25 sensei cooldown, 09-02
M1) were correct on disk and inert in production for hours to days, because nothing asserts
that the running thing is the committed thing. Gap class G6 in the review: `tsx watch` did
not reload the engine; installed plists drifted from source; the shared checkout could not
fast-forward so a daily job kept running last month's code; a sub-bundle fix landed while
the superproject's gitlink still pinned the old commit.

WARN-only (spectator). Every probe distinguishes "measured and fine" from "could not
measure" — an unreadable `lsof`, a service that is not listening, or a repo we cannot
query is a WARN naming the gap, never an OK.

  services      for each long-running service (port -> name): the listening PID's start time
                versus the newest mtime of the source tree it was started from. Source is
                resolved from the process argv FIRST (three of the five dashboard plists have
                no WorkingDirectory, so cwd is `/` — Sol 5.6 review finding 10), cwd second.
                WARN `source-newer` when a source file is newer than the process by more than
                `grace_hours`; WARN `dist-stale` when the tree has both src/ and dist/ and src
                is newer than dist (a frontend change that was never rebuilt).
  sub-bundles   each `.gitmodules` path: WARN `gitlink-drift` when the superproject pins a
                commit other than the submodule's HEAD; WARN `behind-origin` when the
                submodule's HEAD is behind its origin default branch (local refs only —
                doctor never fetches).
  main checkout WARN `behind-origin` when HEAD..origin/work exceeds `behind_limit`; WARN
                `fetch-stale` when FETCH_HEAD is older than `fetch_stale_hours` (then the
                behind count is a floor, not a fact).

Every external call goes through an injectable `run(argv) -> (rc, stdout)` so the tests
never touch a live process table or repository.
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str]], tuple[int, str]]

LABEL = "activation-drift"
SUBPROCESS_TIMEOUT = 15

#: port -> service name. The five long-running services the review names.
DEFAULT_SERVICES: dict[int, str] = {
    3001: "order-samurai-api",
    4477: "agent-hq",
    4321: "brain3-dashboard",
    4322: "order-samurai-dashboard",
    4323: "morning-joe-dashboard",
}
#: Code only. The first live run flagged `public/reports/index.json`, a generated
#: `charts/*.html` and `os_bridge_state.json` as "source newer than the process" —
#: data the running service itself writes. A service is stale when its CODE changed.
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".js"}
#: What a build emits; used only for the dist-vs-src comparison.
DIST_SUFFIXES = {".js", ".css", ".html", ".map"}
SKIP_DIRS = {"node_modules", "dist", ".git", "__pycache__", ".venv", ".pytest_cache", "build", "public"}
MAX_FILES = 20_000


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


# ── services ────────────────────────────────────────────────────────────────────

def listening_pid(port: int, run: Runner) -> tuple[int | None, str | None]:
    """(pid, None) when something listens on the port; (None, reason) otherwise.
    A non-zero lsof exit with empty output is 'nothing listening'; an execution
    failure is 'unmeasured' — the two are never collapsed."""
    rc, out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    if rc == 127:
        return None, "lsof not found — cannot measure"
    if out.startswith("__error__"):
        return None, f"lsof failed ({out[10:].strip()}) — cannot measure"
    pids = [ln[1:] for ln in out.splitlines() if ln.startswith("p") and ln[1:].isdigit()]
    if not pids:
        return None, "nothing is listening"
    return int(pids[0]), None


def process_start_and_argv(pid: int, run: Runner) -> tuple[datetime | None, list[str], str | None]:
    """(start_time, argv, cwd) for a PID via `ps` and `lsof -d cwd`; start_time is None
    when it cannot be parsed. Local-time `lstart` is graded into an aware datetime."""
    rc, out = run(["ps", "-o", "lstart=,command=", "-p", str(pid)])
    start: datetime | None = None
    argv: list[str] = []
    if rc == 0 and out.strip():
        line = out.strip().splitlines()[0]
        # lstart is 24 chars: "Sat Aug 29 04:45:27 2026"
        stamp, _, cmd = line[:24], line[24:25], line[24:].strip()
        try:
            start = datetime.strptime(stamp, "%a %b %d %H:%M:%S %Y").astimezone()
        except ValueError:
            start = None
        argv = cmd.split()
    cwd: str | None = None
    rc, out = run(["lsof", "-p", str(pid), "-a", "-d", "cwd", "-Fn"])
    if rc == 0:
        for ln in out.splitlines():
            if ln.startswith("n"):
                cwd = ln[1:].strip()
    return start, argv, cwd


def resolve_source_root(argv: list[str], cwd: str | None, repo_root: Path) -> Path | None:
    """The directory of the first script the process runs from inside `repo_root`.

    argv first: a plist with no WorkingDirectory starts its job with cwd `/`, so cwd alone
    resolves nothing for three of the five services. A relative argv entry (tsx's
    `src/server.ts`) is joined onto cwd. Anything outside the repo (the interpreter,
    node_modules) is skipped."""
    repo_root = repo_root.resolve()
    tokens = argv[1:] if argv else []
    spans = sorted(
        ((start, end) for start in range(len(tokens)) for end in range(start + 1, len(tokens) + 1)),
        key=lambda span: (span[1] - span[0], -span[0]),
        reverse=True,
    )
    for start, end in spans:
        tok = " ".join(tokens[start:end])
        candidate: Path | None = None
        if tok.startswith("/"):
            candidate = Path(tok)
        elif cwd and cwd != "/":
            candidate = Path(cwd) / tok
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        try:
            resolved.relative_to(repo_root)
        except ValueError:
            continue
        if "node_modules" in resolved.parts:
            continue
        return resolved.parent
    return None


def newest_mtime(root: Path, *, skip: set[str] = SKIP_DIRS, max_files: int = MAX_FILES,
                 suffixes: set[str] = SOURCE_SUFFIXES) -> tuple[datetime | None, Path | None, bool]:
    """(newest mtime, that file, truncated?) over the source files under `root`."""
    newest: float = -1.0
    newest_path: Path | None = None
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            if Path(name).suffix not in suffixes:
                continue
            seen += 1
            if seen > max_files:
                return (datetime.fromtimestamp(newest, timezone.utc) if newest >= 0 else None,
                        newest_path, True)
            p = Path(dirpath) / name
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > newest:
                newest, newest_path = m, p
    return (datetime.fromtimestamp(newest, timezone.utc) if newest >= 0 else None,
            newest_path, False)


def service_checks(services: dict[int, str], repo_root: Path, run: Runner, now: datetime,
                   grace_hours: float = 1.0) -> list[dict]:
    rows: list[dict] = []
    for port, name in sorted(services.items()):
        pid, why = listening_pid(port, run)
        if pid is None:
            rows.append(_row("WARN", name, f":{port} — {why}"))
            continue
        start, argv, cwd = process_start_and_argv(pid, run)
        if start is None:
            rows.append(_row("WARN", name, f":{port} pid {pid} — start time unreadable; cannot measure"))
            continue
        root = resolve_source_root(argv, cwd, repo_root)
        if root is None:
            rows.append(_row("WARN", name, f":{port} pid {pid} — no source file inside "
                                           f"{repo_root} in argv {argv[:3]} (cwd {cwd}); cannot measure"))
            continue
        newest, newest_file, truncated = newest_mtime(root)
        if newest is None:
            rows.append(_row("WARN", name, f":{port} — no source files under {root}; cannot measure"))
            continue
        age_h = (newest - start).total_seconds() / 3600.0
        try:
            rel = newest_file.relative_to(repo_root) if newest_file else newest_file
        except ValueError:
            rel = newest_file
        trunc = " (scan truncated)" if truncated else ""
        if age_h > grace_hours:
            rows.append(_row("WARN", f"{name}.source-newer",
                             f":{port} pid {pid} started {start.astimezone(timezone.utc):%Y-%m-%d %H:%M}Z; "
                             f"{rel} is {age_h:.1f}h newer — restart (or rebuild) to activate{trunc}"))
        else:
            rows.append(_row("OK", name, f":{port} pid {pid} started "
                                         f"{start.astimezone(timezone.utc):%Y-%m-%d %H:%M}Z; source is older "
                                         f"than the process (newest {rel}){trunc}"))
        # A frontend tree that was edited but never rebuilt is the same class of inert fix.
        src, dist = root / "src", root / "dist"
        if src.is_dir() and dist.is_dir():
            src_m, src_f, _ = newest_mtime(src)
            dist_m, _, _ = newest_mtime(dist, skip=set(), suffixes=DIST_SUFFIXES)
            if src_m and dist_m and src_m > dist_m + timedelta(minutes=5):
                rows.append(_row("WARN", f"{name}.dist-stale",
                                 f"{root.name}/src is newer than dist by "
                                 f"{(src_m - dist_m).total_seconds() / 3600.0:.1f}h "
                                 f"(newest {src_f.name if src_f else '?'}) — rebuild dist"))
    return rows


# ── git: sub-bundles and the main checkout ─────────────────────────────────────

def _git(run: Runner, repo: Path, *args: str) -> tuple[int, str]:
    return run(["git", "-C", str(repo), *args])


def submodule_paths(repo_root: Path) -> list[str]:
    paths: list[str] = []
    try:
        for line in (repo_root / ".gitmodules").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("path") and "=" in line:
                paths.append(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return paths


def submodule_checks(repo_root: Path, run: Runner) -> list[dict]:
    rows: list[dict] = []
    paths = submodule_paths(repo_root)
    if not paths:
        return [_row("WARN", "sub-bundles", f"no .gitmodules under {repo_root}; cannot measure")]
    for rel in paths:
        rc, out = _git(run, repo_root, "ls-tree", "HEAD", rel)
        gitlink = out.split()[2] if rc == 0 and len(out.split()) >= 3 else None
        sub = repo_root / rel
        rc2, head = _git(run, sub, "rev-parse", "HEAD")
        head = head.strip() if rc2 == 0 else ""
        if not gitlink or not head:
            rows.append(_row("WARN", f"sub-bundles.{Path(rel).name}",
                             f"{rel}: gitlink {gitlink or '?'} / checkout {head[:12] or '?'} unreadable "
                             f"— cannot measure"))
            continue
        if gitlink != head:
            rows.append(_row("WARN", f"sub-bundles.{Path(rel).name}.gitlink-drift",
                             f"{rel}: superproject pins {gitlink[:12]}, checkout is at {head[:12]} — "
                             f"whatever landed in the submodule is not what the superproject records"))
        behind = None
        for ref in ("origin/main", "origin/master"):
            rc3, cnt = _git(run, sub, "rev-list", "--count", f"HEAD..{ref}")
            if rc3 == 0 and cnt.strip().isdigit():
                behind = (ref, int(cnt.strip()))
                break
        if behind is None:
            rows.append(_row("WARN", f"sub-bundles.{Path(rel).name}.behind-origin",
                             f"{rel}: no origin/main or origin/master ref — cannot measure"))
        elif behind[1] > 0:
            rows.append(_row("WARN", f"sub-bundles.{Path(rel).name}.behind-origin",
                             f"{rel}: checkout is {behind[1]} commit(s) behind {behind[0]} — a fix "
                             f"pushed to the sub-bundle has not been pulled here"))
        elif gitlink == head:
            rows.append(_row("OK", f"sub-bundles.{Path(rel).name}",
                             f"{rel}: gitlink == checkout == {behind[0]} ({head[:12]})"))
    return rows


def main_checkout_checks(main: Path, run: Runner, now: datetime, behind_limit: int = 20,
                         fetch_stale_hours: float = 24.0, upstream: str = "origin/work") -> list[dict]:
    rows: list[dict] = []
    if not (main / ".git").exists():
        return [_row("WARN", "main-checkout", f"{main} is not a git checkout; cannot measure")]
    fetch_head = main / ".git" / "FETCH_HEAD"
    fetch_age_h: float | None = None
    try:
        fetch_age_h = (now - datetime.fromtimestamp(fetch_head.stat().st_mtime, timezone.utc)).total_seconds() / 3600.0
    except OSError:
        pass
    rc, cnt = _git(run, main, "rev-list", "--count", f"HEAD..{upstream}")
    if rc != 0 or not cnt.strip().isdigit():
        rows.append(_row("WARN", "main-checkout.behind-origin",
                         f"{main}: cannot count HEAD..{upstream} ({cnt.strip()[:80] or 'git failed'})"))
    else:
        behind = int(cnt.strip())
        floor = " (a floor — see fetch-stale)" if fetch_age_h is not None and fetch_age_h > fetch_stale_hours else ""
        if behind > behind_limit:
            rows.append(_row("WARN", "main-checkout.behind-origin",
                             f"{main} is {behind} commits behind {upstream}{floor} — every daily job "
                             f"that runs from it runs old code; resync per docs/solutions/workflow-issues/"
                             f"resync-shared-checkout-to-origin-work-2026-09-01.md"))
        else:
            rows.append(_row("OK", "main-checkout.behind-origin",
                             f"{main} is {behind} commit(s) behind {upstream}{floor}"))
    if fetch_age_h is None:
        rows.append(_row("WARN", "main-checkout.fetch-stale",
                         f"{main}: no FETCH_HEAD — never fetched; behind-ness unknown"))
    elif fetch_age_h > fetch_stale_hours:
        rows.append(_row("WARN", "main-checkout.fetch-stale",
                         f"{main}: last fetch {fetch_age_h:.0f}h ago — the behind count above is a floor"))
    else:
        rows.append(_row("OK", "main-checkout.fetch-stale", f"{main}: last fetch {fetch_age_h:.1f}h ago"))
    return rows


# ── family entry point ──────────────────────────────────────────────────────────

def run_checks(*, services: dict[int, str] | None = None, repo_root: Path | None = None,
               main_checkout: Path | None = None, run: Runner | None = None,
               now: datetime | None = None, grace_hours: float = 1.0) -> list[dict]:
    run = run or default_runner
    now = now or datetime.now(timezone.utc)
    main = main_checkout or Path(os.environ.get("AGENTICA_MAIN_CHECKOUT", str(Path.home() / "AgenticaOS")))
    repo = repo_root or main
    rows: list[dict] = []
    rows += service_checks(services if services is not None else DEFAULT_SERVICES, repo, run, now, grace_hours)
    rows += submodule_checks(main, run)
    rows += main_checkout_checks(main, run, now)
    return rows


if __name__ == "__main__":
    for r in run_checks():
        print(f"[{r['status']}] {r['label']}: {r['detail']}")
