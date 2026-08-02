"""Unbounded_Wait_Count -- deterministic extraction of the /timeout-audit skill's
core check (2026-08-01 metric-gap remediation, phase D2).

Two violation classes, scanned across Governance's Python runtime scripts:

  1. UNBOUNDED WAIT LOOP -- a `while`/`until`-style loop containing a `sleep(...)`
     call with no visible deadline (no `break`, `timeout`/`deadline` reference, or
     clock read anywhere in the loop body) -- the *Release It!* "waits forever
     under load" bug that becomes a permanent hung thread.
  2. UNTIMED REMOTE CALL -- requests.get/post/put/delete/patch/head,
     urllib.request.urlopen, subprocess.run/call/check_output/check_call, or
     socket.create_connection with no `timeout=` keyword anywhere on the call's
     line or its next few continuation lines.

This is a deliberately narrow, regex-based heuristic subset of the full
~/.claude/skills/timeout-audit sweep (which also covers TS/JS, DB/queue clients,
and confirms each hit at the call site before reporting it) -- not a replacement
for that skill's broader manual review, just the part that is mechanically
checkable without an LLM in the loop. This is a SCAN, not a confirmed-finding
report: a timeout set on a wrapping client/Session, or a call already inside a
bounded retry, can still false-positive here.

Registered observational (2026-08-01): this script is a standalone, tested,
callable scanner (`scan_tree`) -- wiring its count into the live dashboard
payload is a follow-on step, not part of this session's Files-touched scope.
"""
from __future__ import annotations

import re
from pathlib import Path

_THIS = Path(__file__).resolve()
_OS_ROOT = _THIS.parents[1]
_GOV_ROOT = _OS_ROOT.parent

# Directories never scanned -- vendored/generated/state trees.
_SKIP_DIR_NAMES = {"node_modules", "sub-bundles", "dist", "build", ".git",
                   "__pycache__", ".tmp", "state"}

_WHILE_RE = re.compile(r"^\s*while\b.*:\s*$", re.MULTILINE)
_SLEEP_RE = re.compile(r"\bsleep\s*\(")
_DEADLINE_HINT_RE = re.compile(
    r"\bbreak\b|\btimeout\b|\bdeadline\b|time\.time\(\)|time\.monotonic\(\)|datetime\.now\("
)

_REMOTE_CALL_RE = re.compile(
    r"\b(requests\.(get|post|put|delete|patch|head)|"
    r"urllib\.request\.urlopen|"
    r"subprocess\.(run|call|check_output|check_call)|"
    r"socket\.create_connection)\s*\("
)
_TIMEOUT_KW_RE = re.compile(r"\btimeout\s*=")


def _iter_python_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIR_NAMES for part in p.parts):
            continue
        yield p


def _loop_body_lines(lines: list[str], start_idx: int) -> list[str]:
    """Lines belonging to the while-loop starting at lines[start_idx], by
    indentation -- a simple, deterministic block extraction, not a full AST."""
    header_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    body: list[str] = []
    for line in lines[start_idx + 1:]:
        if line.strip() == "":
            body.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= header_indent:
            break
        body.append(line)
    return body


def scan_unbounded_wait_loops(path: Path) -> list[tuple[int, str]]:
    """(line_no, snippet) for each while-loop containing sleep(...) with no
    break/timeout/deadline/clock-read hint anywhere in its body."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _WHILE_RE.match(line):
            continue
        body_text = "\n".join(_loop_body_lines(lines, i))
        if _SLEEP_RE.search(body_text) and not _DEADLINE_HINT_RE.search(body_text):
            findings.append((i + 1, line.strip()))
    return findings


def scan_untimed_remote_calls(path: Path) -> list[tuple[int, str]]:
    """(line_no, snippet) for each remote/blocking call with no `timeout=`
    on its line or next few continuation lines (catches a kwarg wrapped
    across a multi-line call)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _REMOTE_CALL_RE.search(line):
            continue
        window = "\n".join(lines[i:i + 6])
        if not _TIMEOUT_KW_RE.search(window):
            findings.append((i + 1, line.strip()))
    return findings


def scan_tree(root: Path) -> dict:
    wait_findings: dict[str, list] = {}
    remote_findings: dict[str, list] = {}
    for p in _iter_python_files(root):
        w = scan_unbounded_wait_loops(p)
        if w:
            wait_findings[str(p.relative_to(root))] = w
        r = scan_untimed_remote_calls(p)
        if r:
            remote_findings[str(p.relative_to(root))] = r
    wait_count = sum(len(v) for v in wait_findings.values())
    remote_count = sum(len(v) for v in remote_findings.values())
    return {
        "unbounded_wait_loops": wait_findings,
        "untimed_remote_calls": remote_findings,
        "wait_count": wait_count,
        "remote_count": remote_count,
        "count": wait_count + remote_count,
    }


def main() -> int:
    r = scan_tree(_GOV_ROOT)
    print(f"Unbounded_Wait_Count: {r['count']} finding(s) "
          f"({r['wait_count']} unbounded wait loops, {r['remote_count']} untimed remote calls)")
    for path, hits in sorted(r["unbounded_wait_loops"].items()):
        for lineno, snippet in hits:
            print(f"  [wait]   {path}:{lineno}: {snippet}")
    for path, hits in sorted(r["untimed_remote_calls"].items()):
        for lineno, snippet in hits:
            print(f"  [remote] {path}:{lineno}: {snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
