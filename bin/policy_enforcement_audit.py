#!/usr/bin/env python3
"""Deterministic policy-enforcement-audit mechanism.

The mechanical core of the /policy-enforcement-audit skill, extracted as a
deterministic, testable mechanism. The skill's judgement-free work — enumerate
policy files, find readers, classify readers as ENFORCER or OBSERVER — is pure
rule logic, so it runs faster and ships with a real eval
(tests/test_policy_enforcement_audit.py) instead of an unverifiable LLM pass.

What stays LLM: deciding *how* to wire enforcement for a complex policy file
where the fix requires refactoring hooks that touch multiple subsystems. The
`needs_review` list surfaces those for the skill or a human to judge.

Serves metric: metric:sword:Rule_Violations

Idempotency: read-only scan — never modifies any policy or hook file. Running
twice on identical input yields identical output.

Usage:
    python bin/policy_enforcement_audit.py [--out PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_REPORT_PATH = Path.home() / ".claude" / "data" / "policy_enforcement_audit.json"

# Timeout for any future subprocess calls — unused in the pure-Python reader
# implementation but kept as a named constant per the reference pattern.
SCAN_TIMEOUT_S = 30

# Globs that enumerate policy files from known agent-OS locations.
POLICY_GLOBS = [
    "~/.claude/safety/*.json",
    "~/.claude/data/*allowlist*.json",
    "~/.claude/data/*baseline*.json",
    "~/.claude/data/*threshold*.json",
    "~/.claude/data/*rules*.json",
    "~/.claude/data/*policies*.json",
]

# Directories to search for readers of each policy file.
SEARCH_DIRS = [
    os.path.expanduser("~/.claude/scripts"),
    os.path.expanduser("~/.claude/hooks"),
    os.path.expanduser("~/.claude/execution"),
    r"C:\Users\jemak\Desktop\Agentica OS\Governance",
]

# File extensions considered for reader search — text-based code/config only.
READER_EXTENSIONS = {".py", ".js", ".ts", ".sh", ".json", ".yaml", ".yml", ".md"}

# ---------------------------------------------------------------------------
# Classification patterns (pure)
# ---------------------------------------------------------------------------

# ENFORCER: reader uses policy to actually block/allow.
# Patterns are applied to the SNIPPET (±2 lines around the policy filename reference),
# NOT the full file content — prevents false positives from incidental patterns in
# unrelated functions (PE-02, PE-03, PE-05 adversarial review findings).
ENFORCER_PATTERNS = [
    r"sys\.exit\([1-9]",                                    # non-zero exit on violation
    r"sys\.exit\(2\)",                                      # explicit block exit code
    r"\braise\b.*(polic|block|violat|deny|[Pp]ermission)",  # policy-intent raise only
    r"return\s+False",                                      # gate returns deny
    r"\bblock\b.*policy",                                   # explicit block call
    r"\bdeny\b",
    r"\babort\b",
    r"\"action\":\s*\"block\"",                            # JSON action block
]

# OBSERVER: reader only reads/scores/logs/reports
OBSERVER_PATTERNS = [
    r"\.write\(",                  # writes a file
    r"\.append\(",                 # appends to log
    r"json\.dump",                 # serializes output
    r"\bscore\b",                  # scores a metric
    r"\bcount\b",                  # counts occurrences
    r"\blogger\.",                 # logging call
    r"\blogging\.",
    r"print\(",                    # prints (no exit)
    r"\"status\":\s*\"",           # status field update
    r"\.jsonl",                    # writes to jsonl
]


# ---------------------------------------------------------------------------
# Pure classification functions
# ---------------------------------------------------------------------------

def classify_reader(code_snippet: str) -> str:
    """Return 'ENFORCER' or 'OBSERVER' based on code patterns.

    A reader is an ENFORCER if it has ANY enforcer pattern.
    Otherwise OBSERVER. Pure function — takes the code text, returns the verdict.
    """
    for pat in ENFORCER_PATTERNS:
        if re.search(pat, code_snippet):
            return "ENFORCER"
    return "OBSERVER"


def suggest_fix(policy_type: str) -> str:
    """Suggest where enforcement should be wired.

    Deterministic mapping from policy_type stem to a concrete wiring suggestion.
    Pure function — no I/O.
    """
    if "protected_files" in policy_type or "blocked_paths" in policy_type:
        return "Wire into PreToolUse sync hook in settings.json (blocks Write/Edit on protected paths)"
    if "allowlist" in policy_type:
        return "Wire into validation gate that checks tool against allowlist before execution"
    if "threshold" in policy_type:
        return "Wire into metric gate that blocks action when threshold exceeded"
    return "Add a sync gate that returns non-zero exit on violation"


# ---------------------------------------------------------------------------
# Assembly (pure)
# ---------------------------------------------------------------------------

def _verdict_for(readers: list[dict]) -> str:
    """Derive verdict from a list of ReaderRecord dicts.

    - 'NOT_READ':                  readers is empty
    - 'DECLARED_BUT_UNENFORCED':   readers exist but all are OBSERVER
    - 'ENFORCED':                  at least one reader is ENFORCER
    """
    if not readers:
        return "NOT_READ"
    if any(r["reader_type"] == "ENFORCER" for r in readers):
        return "ENFORCED"
    return "DECLARED_BUT_UNENFORCED"


def build_report(
    *,
    policy_files: list[dict],
    readers_map: dict[str, list[dict]],
    generated_at: str,
) -> dict:
    """Assemble the canonical report dict from already-classified findings.

    `generated_at` is injected (not read from the clock here) so the function is
    pure and the idempotency eval can hold it constant.
    """
    findings: list[dict] = []
    for pf in policy_files:
        readers = readers_map.get(pf["path"], [])
        verdict = _verdict_for(readers)
        findings.append(
            {
                "policy_file": pf["path"],
                "policy_type": pf["policy_type"],
                "keys": pf["keys"],
                "readers": readers,
                "has_enforcer": any(r["reader_type"] == "ENFORCER" for r in readers),
                "verdict": verdict,
                "fix_suggestion": suggest_fix(pf["policy_type"]) if verdict != "ENFORCED" else "",
            }
        )

    enforced = sum(1 for f in findings if f["verdict"] == "ENFORCED")
    unenforced = sum(1 for f in findings if f["verdict"] == "DECLARED_BUT_UNENFORCED")
    not_read = sum(1 for f in findings if f["verdict"] == "NOT_READ")
    needs_review = [f["policy_file"] for f in findings if f["verdict"] == "DECLARED_BUT_UNENFORCED"]

    return {
        "generated_at": generated_at,
        "policies_scanned": len(policy_files),
        "findings": findings,
        "counts": {
            "enforced": enforced,
            "unenforced": unenforced,
            "not_read": not_read,
        },
        "needs_review": needs_review,
    }


# ---------------------------------------------------------------------------
# Real I/O functions (pure Python — no subprocess grep)
# Must appear before run_audit so they can be used as default argument values.
# ---------------------------------------------------------------------------

def _real_policy_files() -> list[dict]:
    """Enumerate policy files from standard agent-OS locations."""
    import glob

    results: list[dict] = []
    for g in POLICY_GLOBS:
        for p in glob.glob(os.path.expanduser(g)):
            try:
                data = json.loads(Path(p).read_text(encoding="utf-8"))
                keys = list(data.keys()) if isinstance(data, dict) else []
                results.append(
                    {
                        "path": p,
                        "policy_type": Path(p).stem,
                        "keys": keys,
                    }
                )
            except (OSError, ValueError):
                pass
    return results


def _real_readers(policy_path: str) -> list[dict]:
    """Find files that reference this policy file and classify each reader.

    Uses pure Python (os.walk + str.find) — no subprocess grep, so no new
    allowlist entries are required.
    """
    filename = Path(policy_path).name
    stem = Path(policy_path).stem  # also search for stem in case imported without extension

    results: list[dict] = []
    for search_dir in SEARCH_DIRS:
        if not os.path.isdir(search_dir):
            continue
        for dirpath, _dirnames, filenames in os.walk(search_dir):
            for fname in filenames:
                if Path(fname).suffix not in READER_EXTENSIONS:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    content = Path(fpath).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if filename not in content and stem not in content:
                    continue
                # Extract the snippet (±2 lines around the policy filename reference)
                # THEN classify only the snippet — prevents false-positives from
                # incidental patterns (return False, raise KeyError, etc.) in
                # unrelated functions elsewhere in the file (PE-03 fix).
                lines = content.splitlines()
                snippet_lines: list[str] = []
                for i, line in enumerate(lines):
                    if filename in line or stem in line:
                        start = max(0, i - 2)
                        end = min(len(lines), i + 3)
                        snippet_lines = lines[start:end]
                        break
                snippet = "\n".join(snippet_lines) if snippet_lines else content[:300]
                reader_type = classify_reader(snippet)
                # Find the evidence pattern that determined classification
                evidence_pat = ""
                patterns_to_check = ENFORCER_PATTERNS if reader_type == "ENFORCER" else OBSERVER_PATTERNS
                for pat in patterns_to_check:
                    if re.search(pat, snippet):
                        evidence_pat = pat
                        break
                if not evidence_pat:
                    evidence_pat = "(filename reference)"
                results.append(
                    {
                        "reader_path": fpath,
                        "reader_type": reader_type,
                        "evidence": evidence_pat,
                        "snippet": snippet,
                    }
                )
    return results


# ---------------------------------------------------------------------------
# Orchestration (testable via injected fns; no shell in tests)
# ---------------------------------------------------------------------------

def run_audit(
    *,
    policy_files_fn: Callable[[], list[dict]] = _real_policy_files,
    readers_fn: Callable[[str], list[dict]] = _real_readers,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Enumerate policy files, find readers, classify, and assemble the report.

    Pure given its injected I/O functions — the eval passes fixtures so it
    never touches the real filesystem. Read-only: it scans and reports, never
    mutating any policy file, which makes re-running inherently safe (idempotent).
    """
    policy_files = policy_files_fn()
    readers_map: dict[str, list[dict]] = {}
    for pf in policy_files:
        readers_map[pf["path"]] = readers_fn(pf["path"])

    return build_report(
        policy_files=policy_files,
        readers_map=readers_map,
        generated_at=now_fn(),
    )


def write_report(report: dict, path: Path) -> None:
    """Write the report dict to `path` as stable, sorted JSON (parent dirs created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict, out_path: Path) -> str:
    c = report["counts"]
    lines = [
        f"Policy enforcement audit — {report['generated_at']}",
        f"  policies scanned: {report['policies_scanned']}  ·  "
        f"enforced: {c['enforced']}  ·  "
        f"unenforced: {c['unenforced']}  ·  "
        f"not_read: {c['not_read']}",
    ]
    unenforced = [f for f in report["findings"] if f["verdict"] == "DECLARED_BUT_UNENFORCED"]
    if unenforced:
        lines.append("\nDECLARED_BUT_UNENFORCED (needs wiring):")
        for f in unenforced:
            lines.append(f"  {f['policy_file']}")
            lines.append(f"    → {f['fix_suggestion']}")
    not_read = [f for f in report["findings"] if f["verdict"] == "NOT_READ"]
    if not_read:
        lines.append("\nNOT_READ (no reader found):")
        for f in not_read:
            lines.append(f"  {f['policy_file']}")
    lines.append(f"\nWrote {out_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic policy-enforcement-audit mechanism"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_REPORT_PATH,
        help="where to write policy_enforcement_audit.json",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    report = run_audit()

    try:
        write_report(report, args.out)
    except OSError as exc:
        print(f"policy-enforcement-audit: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(report, indent=2, sort_keys=True) if args.json
        else _format_report(report, args.out)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
