#!/usr/bin/env python3
"""Deterministic codebase-cleanup-deps-audit mechanism.

The mechanical core of the /codebase-cleanup-deps-audit skill, extracted as a
deterministic, testable mechanism (RONIN-DETERMINIZATION-PLAN.md, candidate #4:
high-frequency, mechanical scan core). The skill's judgement-free work — run the
scanners, parse their output, classify findings — is pure rule logic, so it runs
faster and ships with a real eval (tests/test_codebase_deps_audit.py) instead of a
67%-success LLM remediation.

This mechanism is the PRODUCER of `dependency_audit.json` — the exact file the
already-determinized pip-safe-upgrade mechanism consumes (bin/pip_safe_upgrade.py,
DEFAULT_AUDIT_PATH). Determinizing it closes that loop end to end.

What stays LLM: the genuinely ambiguous tail — non-permissive / unknown licences a
human must clear, and CVEs with no clean fix version. Those surface in the audit
under `needs_review`, for a human or the /codebase-cleanup-deps-audit skill to judge.

Allowlist note: every scanner runs via `python -m` (`python -m pip ...`,
`python -m pip_audit ...`), so this mechanism needs NO new tool-allowlist entries
beyond the dojo's existing `Bash(python:*)`. Licence scanning is pure importlib —
no shell at all. The npm path is left as an injected hook (off by default); wiring
it is the only thing that would require adding `Bash(npm audit:*)`.

Usage:
    python bin/codebase_deps_audit.py [--out PATH] [--json] [--no-licenses]

Default is scan-and-write (no dependency mutation — a read-only audit): runs the
scanners, classifies findings, writes dependency_audit.json, prints the report.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

# Same canonical location pip-safe-upgrade reads from — the two mechanisms align.
DEFAULT_AUDIT_PATH = Path.home() / ".claude" / "data" / "dependency_audit.json"

# Scanner subprocess timeout — remote index / advisory calls must never hang.
SCAN_TIMEOUT_S = 300

# Licences that clear automatically. Anything outside this set (or empty/unknown)
# is flagged for the LLM/human judgement tail rather than auto-cleared.
PERMISSIVE_LICENCES = frozenset(
    {"mit", "bsd", "apache", "apache 2.0", "apache-2.0", "isc", "python", "psf",
     "bsd-3-clause", "bsd-2-clause", "mpl-2.0", "unlicense", "zlib"}
)

# Copyleft markers — flagged explicitly (distinct from "unknown") so a reviewer
# sees *why* it needs a look.
COPYLEFT_MARKERS = ("gpl", "agpl", "lgpl", "gnu", "cc-by-sa", "epl", "cddl")


# ---------------------------------------------------------------------------
# Parsing (pure)
# ---------------------------------------------------------------------------

def parse_pip_outdated(stdout: str) -> list[dict]:
    """Parse `pip list --outdated --format json` into upgrade candidates.

    Returns [{"name", "version", "latest"}], sorted by name for determinism.
    Tolerant of empty / malformed output (returns []), so a scanner hiccup
    degrades to "nothing outdated" rather than crashing the mechanism.
    """
    try:
        rows = json.loads(stdout or "[]")
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "version": row.get("version", "unknown"),
                "latest": row.get("latest_version", row.get("latest", "latest")),
            }
        )
    return sorted(out, key=lambda r: r["name"].lower())


def parse_pip_audit(stdout: str) -> list[dict]:
    """Parse `pip-audit --format json` into CVE findings.

    Accepts both pip-audit shapes: the newer `{"dependencies": [...]}` envelope
    and the older top-level list. Each dependency with a non-empty `vulns` list
    becomes one finding {"package", "version", "vuln_ids", "vuln_count"} — the
    exact shape triage() in pip_safe_upgrade expects. Sorted by package name.
    """
    try:
        doc = json.loads(stdout or "[]")
    except ValueError:
        return []

    deps = doc.get("dependencies", []) if isinstance(doc, dict) else doc
    if not isinstance(deps, list):
        return []

    findings: list[dict] = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        name = dep.get("name")
        if not name:
            continue
        vuln_ids = sorted(
            v.get("id") for v in vulns if isinstance(v, dict) and v.get("id")
        )
        findings.append(
            {
                "package": name,
                "version": dep.get("version", "unknown"),
                "vuln_ids": vuln_ids,
                "vuln_count": len(vuln_ids),
            }
        )
    return sorted(findings, key=lambda f: f["package"].lower())


def classify_licence(licence: str | None) -> str:
    """Classify a licence string as 'permissive', 'copyleft', or 'unknown'.

    Pure rule logic — the deterministic core of the licence scan. 'permissive'
    auto-clears; 'copyleft' and 'unknown' are surfaced for review.
    """
    if not licence or not licence.strip():
        return "unknown"
    low = licence.strip().lower()
    if any(marker in low for marker in COPYLEFT_MARKERS):
        return "copyleft"
    # First token handles "MIT License", "BSD-3-Clause", "Apache Software License".
    if low in PERMISSIVE_LICENCES or low.split()[0] in PERMISSIVE_LICENCES:
        return "permissive"
    return "unknown"


def scan_licences(distributions: Iterable[tuple[str, str, str | None]]) -> list[dict]:
    """Flag non-permissive / unknown licences from installed package metadata.

    `distributions` is an iterable of (name, version, licence_string) — injected
    so the eval supplies fixtures and the mechanism never depends on what happens
    to be installed. Returns only the flagged packages (permissive ones are clean
    and omitted), sorted by name. Pure: no shell, no I/O.
    """
    flags: list[dict] = []
    for name, version, licence in distributions:
        if not name:
            continue
        verdict = classify_licence(licence)
        if verdict == "permissive":
            continue
        flags.append(
            {
                "name": name,
                "version": version or "unknown",
                "licence": (licence or "").strip() or "UNKNOWN",
                "flag": verdict,
            }
        )
    return sorted(flags, key=lambda f: f["name"].lower())


# ---------------------------------------------------------------------------
# Assembly (pure)
# ---------------------------------------------------------------------------

def build_audit(
    *,
    pip_outdated: list[dict],
    pip_cves: list[dict],
    licence_flags: list[dict],
    generated_at: str,
    npm_audits: list[dict] | None = None,
) -> dict:
    """Assemble the canonical audit dict from already-parsed findings.

    Shape matches what pip_safe_upgrade.triage() reads (pip_outdated / pip_cves)
    plus this mechanism's own licence findings. `needs_review` separates the
    judgement tail (copyleft/unknown licences, CVEs without a clean fix) from the
    auto-clearable findings — the findings/action split the plan calls for.

    `generated_at` is injected (not read from the clock here) so the function is
    pure and the idempotency eval can hold it constant.
    """
    needs_review = {
        "licences": [f for f in licence_flags if f["flag"] in ("copyleft", "unknown")],
        "cves": [c for c in pip_cves],  # every CVE wants a human/skill confirmation
    }
    return {
        "generated_at": generated_at,
        "pip_outdated": pip_outdated,
        "pip_cves": pip_cves,
        "npm_audits": npm_audits or [],
        "licence_flags": licence_flags,
        "needs_review": needs_review,
        "counts": {
            "outdated": len(pip_outdated),
            "cves": len(pip_cves),
            "licence_flags": len(licence_flags),
            "needs_review": len(needs_review["licences"]) + len(needs_review["cves"]),
        },
    }


# ---------------------------------------------------------------------------
# Real scanners (python -m only — no new allowlist entries)
# ---------------------------------------------------------------------------

def _real_pip_outdated() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "list", "--outdated", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=SCAN_TIMEOUT_S,
    )
    return proc.stdout


def _real_pip_audit() -> str:
    """Run pip-audit as a module. Returns its JSON stdout, or '[]' if unavailable.

    pip-audit exits non-zero when it finds vulnerabilities (that's success, not
    failure) and may be absent; either way we hand the raw stdout to the parser,
    which tolerates empty input. Never raises on a missing scanner.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return "[]"
    return proc.stdout or "[]"


def _installed_licences() -> list[tuple[str, str, str | None]]:
    """Read (name, version, licence) for installed distributions. Best-effort."""
    try:
        from importlib import metadata
    except Exception:
        return []

    rows: list[tuple[str, str, str | None]] = []
    for dist in metadata.distributions():
        meta = dist.metadata
        name = meta["Name"] if "Name" in meta else None
        if not name:
            continue
        licence = meta["License"] if "License" in meta else None
        if not licence or licence in ("UNKNOWN", ""):
            # Fall back to the licence classifier trove, e.g.
            # "License :: OSI Approved :: MIT License".
            classifiers = meta.get_all("Classifier") or []
            lic_classifiers = [c for c in classifiers if c.startswith("License ::")]
            if lic_classifiers:
                licence = lic_classifiers[0].split("::")[-1].strip()
        rows.append((name, meta["Version"] if "Version" in meta else "unknown", licence))
    return rows


# ---------------------------------------------------------------------------
# Orchestration (testable via injected fns; no shell in tests)
# ---------------------------------------------------------------------------

def run_audit(
    *,
    pip_outdated_fn: Callable[[], str] = _real_pip_outdated,
    pip_audit_fn: Callable[[], str] = _real_pip_audit,
    licence_fn: Callable[[], list[tuple[str, str, str | None]]] = _installed_licences,
    npm_audit_fn: Callable[[], list[dict]] | None = None,
    include_licences: bool = True,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Run every scanner, classify, and assemble the audit dict.

    Pure given its injected scanner functions — the eval passes fixtures so it
    never shells out. Read-only: it scans and reports, never mutating any
    dependency, which is what makes re-running it inherently safe (idempotent).
    """
    pip_outdated = parse_pip_outdated(pip_outdated_fn())
    pip_cves = parse_pip_audit(pip_audit_fn())
    licence_flags = scan_licences(licence_fn()) if include_licences else []
    npm_audits = npm_audit_fn() if npm_audit_fn is not None else []

    return build_audit(
        pip_outdated=pip_outdated,
        pip_cves=pip_cves,
        licence_flags=licence_flags,
        npm_audits=npm_audits,
        generated_at=now_fn(),
    )


def write_audit(audit: dict, path: Path) -> None:
    """Write the audit dict to `path` as stable, sorted JSON (parent dirs created)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_report(audit: dict, out_path: Path) -> str:
    c = audit["counts"]
    lines = [
        f"Dependency audit — {audit['generated_at']}",
        f"  outdated: {c['outdated']}  ·  CVEs: {c['cves']}  ·  "
        f"licence flags: {c['licence_flags']}  ·  needs review: {c['needs_review']}",
    ]
    if audit["pip_cves"]:
        lines.append("\nCVEs:")
        for cve in audit["pip_cves"]:
            ids = ", ".join(cve["vuln_ids"]) or "?"
            lines.append(f"  {cve['package']} {cve['version']}  — {ids}")
    if audit["needs_review"]["licences"]:
        lines.append("\nLicences needing review:")
        for f in audit["needs_review"]["licences"]:
            lines.append(f"  [{f['flag']}] {f['name']} {f['version']}  — {f['licence']}")
    lines.append(f"\nWrote {out_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic codebase-cleanup-deps-audit mechanism"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_AUDIT_PATH,
                        help="where to write dependency_audit.json")
    parser.add_argument("--no-licences", action="store_true",
                        help="skip the (pure, shell-free) licence scan")
    parser.add_argument("--json", action="store_true", help="emit the audit as JSON")
    args = parser.parse_args(argv)

    audit = run_audit(include_licences=not args.no_licences)

    try:
        write_audit(audit, args.out)
    except OSError as exc:
        print(f"deps-audit: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(audit, indent=2, sort_keys=True) if args.json
          else _format_report(audit, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
