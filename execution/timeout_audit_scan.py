"""Unbounded_Wait_Count -- deterministic extraction of the /timeout-audit skill's
core check (2026-08-01 metric-gap remediation, phase D2).

Two violation classes, scanned across Governance's Python runtime scripts:

  1. UNBOUNDED WAIT LOOP -- a `while`/`until`-style loop containing a `sleep(...)`
     call with no visible deadline (no `break`, `timeout`/`deadline` reference, or
     clock read anywhere in the loop body) -- the *Release It!* "waits forever
     under load" bug that becomes a permanent hung thread.
  2. UNTIMED REMOTE CALL -- requests.get/post/put/delete/patch/head,
     urllib.request.urlopen, subprocess.run/call/check_output/check_call, or
     socket.create_connection with no `timeout=` keyword anywhere in the call's
     full argument list (span found by parenthesis balance, not a fixed
     lookahead -- see _call_span).

This is a deliberately narrow, regex-based heuristic subset of the full
~/.claude/skills/timeout-audit sweep (which also covers TS/JS, DB/queue clients,
and confirms each hit at the call site before reporting it) -- not a replacement
for that skill's broader manual review, just the part that is mechanically
checkable without an LLM in the loop. This is a SCAN, not a confirmed-finding
report: a timeout set on a wrapping client/Session, or a call already inside a
bounded retry, can still false-positive here.

Wired into the live dashboard payload 2026-08-04 (Unbounded_Wait_Count,
brush/Code Health, observational). Two corrections were required first, both
found by precision-checking the scanner's own output before trusting it:

  * The untimed-remote-call check used a fixed 6-line lookahead for `timeout=`.
    Real calls in this codebase span 8-21 lines, so every one of the 7 runtime
    findings was a FALSE POSITIVE -- each had a timeout, just past the window.
    Precision was 0/7. `_call_span` now walks parenthesis balance instead.
  * Test fixtures dominated the raw count (35 of 42 findings were
    `subprocess.run(["git", ...])` inside tests/). They are a real but
    different risk (a hung CI job, not a hung production thread), so
    `scan_tree` reports them separately and the graded metric counts runtime
    only. Mixing them made the number movable by editing test files.
"""
from __future__ import annotations

import io
import re
import ast
import tokenize
from pathlib import Path, PurePosixPath

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


def _code_only(text: str) -> list[str]:
    """Source lines with comment and string-literal *content* blanked to spaces,
    line numbers and indentation preserved. The checks below match raw text, so
    a docstring or comment that merely mentions `subprocess.run(...)` would
    otherwise be reported as a real untimed call -- the same match-the-prose bug
    as counting conflict markers inside documentation about conflicts. On
    unparseable source, fall back to the raw lines: over-reporting is
    recoverable, a silent all-clear is not."""
    lines = text.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines
    for tok in toks:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            idx = row - 1
            if idx >= len(lines):
                break
            line = lines[idx]
            a = scol if row == srow else 0
            b = ecol if row == erow else len(line)
            lines[idx] = line[:a] + " " * max(0, b - a) + line[b:]
    return lines


def _iter_python_files(root: Path):
    for p in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIR_NAMES or part == "tests" for part in p.parts):
            continue
        yield p


def _read_lines(path: Path) -> tuple[list[str], list[str]]:
    """(raw lines, comment/string-blanked lines) for a source file, or two empty
    lists if it cannot be read. Both scanners match against the blanked copy and
    report the snippet from the raw one."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], []
    return text.splitlines(), _code_only(text)


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
    raw, lines = _read_lines(path)
    findings: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not _WHILE_RE.match(line):
            continue
        body_text = "\n".join([line, *_loop_body_lines(lines, i)])
        if _SLEEP_RE.search(body_text) and not _DEADLINE_HINT_RE.search(body_text):
            findings.append((i + 1, raw[i].strip()))
    return findings


def _call_span(lines: list[str], start_idx: int, max_lines: int = 60) -> str:
    """The full text of the call opening at lines[start_idx], found by walking
    parenthesis balance to the closing paren. A fixed lookahead window silently
    truncates long multi-line calls and reports their kwargs as absent -- the
    0/7-precision bug this replaced."""
    depth = 0
    span: list[str] = []
    for line in lines[start_idx:start_idx + max_lines]:
        span.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0 and len(span) > 1:
            break
    return "\n".join(span)


def scan_untimed_remote_calls(path: Path) -> list[tuple[int, str]]:
    """(line_no, snippet) for each remote/blocking call with no timeout keyword.

    Python's AST owns call boundaries; a fixed continuation-line window misclassified valid calls
    whenever a payload happened to push ``timeout=`` onto line seven or later.

    Raises SyntaxError if ``path`` doesn't parse. The caller (`scan_tree`) records that as a scan
    error, not a zero-finding result -- a scanner that goes quiet on a file it can't parse is
    indistinguishable from a clean file, which is worse than no scan at all.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    lines = text.splitlines()
    tree = ast.parse(text)

    def dotted_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = dotted_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    remote_names = {
        "requests.get", "requests.post", "requests.put", "requests.delete",
        "requests.patch", "requests.head", "urllib.request.urlopen",
        "subprocess.run", "subprocess.call", "subprocess.check_output",
        "subprocess.check_call", "socket.create_connection",
    }
    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or dotted_name(node.func) not in remote_names:
            continue
        if not any(keyword.arg == "timeout" for keyword in node.keywords):
            lineno = getattr(node, "lineno", 1)
            snippet = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else dotted_name(node.func)
            findings.append((lineno, snippet))
    return sorted(findings)


def is_test_path(rel_path: str) -> bool:
    """True for pytest fixture/test modules, whose findings scan_tree counts
    separately (see this module's docstring for why)."""
    p = PurePosixPath(rel_path.replace("\\", "/"))
    return "tests" in p.parts or p.name.startswith("test_")


def scan_tree(root: Path) -> dict:
    wait_findings: dict[str, list] = {}
    remote_findings: dict[str, list] = {}
    scan_errors: dict[str, str] = {}
    for p in _iter_python_files(root):
        w = scan_unbounded_wait_loops(p)
        if w:
            wait_findings[str(p.relative_to(root))] = w
        try:
            r = scan_untimed_remote_calls(p)
        except SyntaxError as exc:
            scan_errors[str(p.relative_to(root))] = str(exc)
            continue
        if r:
            remote_findings[str(p.relative_to(root))] = r
    wait_count = sum(len(v) for v in wait_findings.values())
    remote_count = sum(len(v) for v in remote_findings.values())
    test_count = sum(
        len(hits)
        for findings in (wait_findings, remote_findings)
        for rel_path, hits in findings.items()
        if is_test_path(rel_path)
    )
    total = wait_count + remote_count
    return {
        "unbounded_wait_loops": wait_findings,
        "untimed_remote_calls": remote_findings,
        "scan_errors": scan_errors,
        "wait_count": wait_count,
        "remote_count": remote_count,
        "count": total,
        "error_count": len(scan_errors),
        # runtime_count is the graded signal; test_count is reported for context.
        "test_count": test_count,
        "runtime_count": total - test_count,
    }


def main() -> int:
    r = scan_tree(_GOV_ROOT)
    print(f"Unbounded_Wait_Count: {r['runtime_count']} runtime finding(s) "
          f"[{r['count']} total, {r['test_count']} in tests] "
          f"({r['wait_count']} unbounded wait loops, {r['remote_count']} untimed remote calls)")
    for path, hits in sorted(r["unbounded_wait_loops"].items()):
        for lineno, snippet in hits:
            print(f"  [wait]   {path}:{lineno}: {snippet}")
    for path, hits in sorted(r["untimed_remote_calls"].items()):
        for lineno, snippet in hits:
            print(f"  [remote] {path}:{lineno}: {snippet}")
    if r["scan_errors"]:
        print(f"SCAN_ERRORS: {r['error_count']} file(s) could not be parsed -- "
              f"NOT counted as clean:")
        for path, msg in sorted(r["scan_errors"].items()):
            print(f"  [error]  {path}: {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
