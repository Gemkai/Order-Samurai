#!/usr/bin/env python3
"""Deterministic Secrets_Detected scrub mechanism (detect->verify; clone of error_triage.py).

The mechanical core of the /security-audit fallback for the Secrets_Detected metric, extracted
as a deterministic, testable mechanism (metrics_path_to_10.md). It does the three things an LLM
loop cannot do reliably:

  1. DETECT  — re-scan the same roots verify_secrets grades and group every leak by source file
               and secret pattern, so each leaking file is named with its exemplar masked value.
  2. VERIFY  — re-measure Secrets_Detected (the count of source files carrying >=1 finding,
               IDENTICAL to the dashboard's sum(verify_secrets.run_checks() FAIL)) and set
               breach_confirmed when that count is at/above the LIVE FAIL threshold.
  3. APPLY (gated) — redaction != un-leak. For each leaking file the mechanism drafts a MANDATORY
               rotation ticket (the exposed credential must be rotated regardless) and can mask the
               secret value in place. This MUTATES, so it defaults to --dry-run and gates --apply;
               the wired reflex runs read-only (dry-run) only.

This is a faithful clone of bin/error_triage.py: pure core (count / classify — inject findings in
tests) + thin real-I/O (reuses agentica_core.verify_secrets so it grades exactly what the dashboard
does) + main() that writes state/secret_scrub.json.

Metric served: metric:sword:Secrets_Detected

Usage:
    python bin/secret_scrub.py [--fail-threshold N] [--json]          # detect + verify (read-only)
    python bin/secret_scrub.py --apply [--tickets-dir DIR]            # draft rotations + redact in place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_STATE_PATH = Path(__file__).resolve().parents[1] / "state" / "secret_scrub.json"
_DEFAULT_TICKETS_DIR = Path(__file__).resolve().parents[1] / "backlog"


# ---------------------------------------------------------------------------
# Pure core (testable via injected findings — no real I/O in tests)
# ---------------------------------------------------------------------------

def source_count(findings: list[dict]) -> int:
    """Number of distinct source files carrying >=1 finding — IDENTICAL to the kernel metric
    (aggregate secret_fails = one FAIL per source from verify_secrets.run_checks). Asserted by
    tests/../test_secret_scrub_drift.py."""
    return len({f.get("source", "") for f in findings})


def _group_by_source(findings: list[dict]) -> list[dict]:
    """Leaks ranked by finding count desc, then source — total, input-order-independent order."""
    by_source: dict[str, list[dict]] = {}
    for f in findings:
        by_source.setdefault(str(f.get("source", "")), []).append(f)
    leaks: list[dict] = []
    for source, fs in sorted(by_source.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        leaks.append({
            "source": source,
            "finding_count": len(fs),
            "patterns": sorted({str(f.get("pattern_name", "")) for f in fs}),
            "masked": sorted(str(f.get("match_masked", "")) for f in fs),
        })
    return leaks


def _rotation_ticket(leak: dict, generated_at: str) -> str:
    """The mandatory rotation ticket for one leaking file. Redaction removes the secret from the
    file, but the credential is already exposed — it MUST be rotated. (redaction != un-leak)"""
    patterns = ", ".join(leak["patterns"]) or "secret"
    return (
        f"# SECRET ROTATION REQUIRED - {leak['source']}\n\n"
        f"- generated: {generated_at}\n"
        f"- file: `{leak['source']}`\n"
        f"- leaked pattern(s): {patterns}  ({leak['finding_count']} finding(s))\n\n"
        "Redacting the value in the file does NOT un-leak it — the credential is already exposed.\n\n"
        "- [ ] Revoke / rotate the affected credential(s) at the provider\n"
        "- [ ] Replace the in-file value with an env var / secret-manager reference\n"
        "- [ ] Confirm the new value is never committed (.gitignore / pre-commit secret scan)\n"
        "- [ ] Re-run `python bin/secret_scrub.py` and confirm the count dropped\n"
    )


def audit(
    findings: list[dict],
    *,
    fail_threshold: float,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
    dry_run: bool = True,
) -> dict:
    """Build the deterministic Secrets_Detected report from already-scanned findings.

    Pure given its inputs. breach_confirmed is the verify gate: True when the leaking-source count
    is at/above FAIL. A completed scan is always a real measurement (count 0 = clean), so there is
    no uncalibrated state — unlike Error_Rate/Chain_Depth_Avg.
    """
    count = source_count(findings)
    breach_confirmed = count >= fail_threshold
    leaks = _group_by_source(findings)
    generated_at = now_fn()
    tickets = [{"source": lk["source"], "body": _rotation_ticket(lk, generated_at)} for lk in leaks]

    return {
        "generated_at": generated_at,
        "metric": "metric:sword:Secrets_Detected",
        "leaking_sources": count,
        "total_findings": len(findings),
        "calibrated": True,
        "fail_threshold": fail_threshold,
        "breach_confirmed": breach_confirmed,
        "verdict": "breach_confirmed" if breach_confirmed else "clean",
        "dry_run": dry_run,
        "top_leak": leaks[0] if leaks else None,
        "leaks": leaks,
        "rotation_tickets": tickets,
    }


# ---------------------------------------------------------------------------
# Mutation (only under --apply) — redact in place + write rotation tickets
# ---------------------------------------------------------------------------

def redact_text(text: str, patterns: list[tuple[str, str]], is_placeholder: Callable[[str], bool]) -> tuple[str, int]:
    """Mask each real secret VALUE with <REDACTED:name> (a placeholder, so a re-scan won't re-flag
    it). For generic_hardcoded_secret the value is group(2) (keep the key name); else group(0).
    Returns (new_text, n_redacted). Idempotent: <REDACTED:...> already matches the placeholder rule."""
    redactions = 0

    def _make_sub(name: str):
        def _sub(m: re.Match) -> str:
            nonlocal redactions
            value = m.group(2) if (name == "generic_hardcoded_secret" and m.lastindex and m.lastindex >= 2) else m.group(0)
            if is_placeholder(value):
                return m.group(0)
            redactions += 1
            return m.group(0).replace(value, f"<REDACTED:{name}>")
        return _sub

    for pattern, name in patterns:
        text = re.sub(pattern, _make_sub(name), text)
    return text, redactions


def _apply(leaks: list[dict], tickets: list[dict], tickets_dir: Path) -> dict:
    """Write rotation tickets and redact secret values in each leaking file. Real I/O — never run
    in tests against real source. Returns a summary of what was written/changed."""
    from agentica_core import verify_secrets  # noqa: E402  (real tree; read on parents[2] in main)

    tickets_dir.mkdir(parents=True, exist_ok=True)
    written_tickets: list[str] = []
    for t in tickets:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", t["source"]).strip("_")[-80:] or "unknown"
        path = tickets_dir / f"needs_rotation_{safe}.md"
        try:
            path.write_text(t["body"], encoding="utf-8")
            written_tickets.append(str(path))
        except OSError:
            pass

    redacted_files: list[dict] = []
    for lk in leaks:
        src = Path(lk["source"])
        try:
            original = src.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        new_text, n = redact_text(original, verify_secrets.SECRET_PATTERNS, verify_secrets._is_placeholder)
        if n and new_text != original:
            try:
                src.write_text(new_text, encoding="utf-8")
                redacted_files.append({"source": lk["source"], "redactions": n})
            except OSError:
                pass
    return {"tickets_written": written_tickets, "redacted_files": redacted_files}


# ---------------------------------------------------------------------------
# Real I/O (called only by main() — never in tests)
# ---------------------------------------------------------------------------

def _governance_root() -> Path:
    """Resolve the Governance root (holds agentica_core) and put it on sys.path. Template caveat
    (error_triage._real_records): the wired mechanism is read_only:true so the reflex runs it from
    the REAL tree (parents[2] == Governance). --apply mutates but is a manual/escalated path."""
    governance_root = Path(__file__).resolve().parents[2]
    if str(governance_root) not in sys.path:
        sys.path.insert(0, str(governance_root))
    return governance_root


def _real_findings() -> list[dict]:
    """Scan the same roots verify_secrets grades, returning raw findings. Reuses agentica_core so
    the leaking-source count re-measures exactly what aggregate.py computes (no drift)."""
    _governance_root()
    from agentica_core import verify_secrets  # noqa: E402
    out: list[dict] = []
    for root in verify_secrets._default_roots():
        if root.exists():
            out.extend(verify_secrets.scan_path(root))
    return out


def _live_fail_threshold() -> float:
    """The effective Secrets_Detected FAIL the dashboard grades on: METRIC_CONFIG value AFTER
    insights._apply_calibration (clamped so calibration can only tighten). Read live, not baked
    (error_triage.py caveat). The drift guard pins this to the kernel value."""
    _governance_root()
    from agentica_core.insights import METRIC_CONFIG  # noqa: E402
    return float(METRIC_CONFIG["Secrets_Detected"]["fail"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(report: dict) -> str:
    lines = [
        f"Secret Scrub - {report['generated_at']}  ({'DRY-RUN' if report['dry_run'] else 'APPLIED'})",
        f"Leaking sources: {report['leaking_sources']}  ({report['total_findings']} findings)  "
        f"verdict: {report['verdict'].upper()}  (fail >= {report['fail_threshold']})",
    ]
    for lk in report["leaks"][:5]:
        lines.append(f"  [{lk['finding_count']}x] {', '.join(lk['patterns'])}  {lk['source']}")
    if report.get("applied"):
        a = report["applied"]
        lines.append(f"Applied: {len(a['tickets_written'])} rotation ticket(s), "
                     f"{len(a['redacted_files'])} file(s) redacted")
    elif report["leaks"]:
        lines.append("(dry-run: re-run with --apply to draft rotation tickets + redact in place)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Windows stdout defaults to cp1252 and can't encode non-ASCII in file paths/secrets; the
    # resulting UnicodeEncodeError would exit non-zero and fall through to the slow LLM skill.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="Deterministic Secrets_Detected scrub mechanism")
    parser.add_argument("--fail-threshold", type=float, default=None,
                        help="leaking-source count at/above which a breach is confirmed "
                             "(default: the live calibrated METRIC_CONFIG value)")
    parser.add_argument("--apply", action="store_true",
                        help="MUTATE: draft rotation tickets + redact secrets in place (default is dry-run)")
    parser.add_argument("--tickets-dir", type=Path, default=_DEFAULT_TICKETS_DIR,
                        help="where to write rotation tickets under --apply")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    fail_threshold = args.fail_threshold if args.fail_threshold is not None else _live_fail_threshold()
    findings = _real_findings()
    report = audit(findings, fail_threshold=fail_threshold, dry_run=not args.apply)

    if args.apply and report["leaks"]:
        report["applied"] = _apply(report["leaks"], report["rotation_tickets"], args.tickets_dir)

    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
