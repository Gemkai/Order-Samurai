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

Allowlist note: Python scanners run via `python -m` (`python -m pip ...`,
`python -m pip_audit ...`), covered by the existing `Bash(python:*)` entry. Licence
scanning is pure importlib. The npm scanner is OFF by default and runs only with
`--npm`: `npm audit` sends the repo-owned lockfiles' dependency graph to the npm
registry — a network call — so wiring it into any unattended invocation requires
explicit approval (a `Bash(npm audit:*)`-class allowlist decision), not just this
script's presence behind an allowlisted interpreter. When enabled it is still
read-only: `--package-lock-only --ignore-scripts`, never running package scripts
or mutating a dependency.

Usage:
    python bin/codebase_deps_audit.py [--out PATH] [--json] [--no-licences] [--npm]

Default is scan-and-write (no dependency mutation — a read-only audit): runs the
scanners, classifies findings, writes dependency_audit.json, prints the report.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

# Same canonical location pip-safe-upgrade reads from — the two mechanisms align.
DEFAULT_AUDIT_PATH = Path.home() / ".claude" / "data" / "dependency_audit.json"

def _resolve_governance_root(script_path: Path | None = None) -> Path:
    """Governance root for npm-project resolution, valid in BOTH layouts.

    A bare `parents[2]` is only correct in the nested live repo
    (Governance/Order Samurai/bin/…); in the flat product pack
    (<pack>/bin/…, exported by extract_public.py) it walks OUT of the pack
    into the surrounding directory. Same bug class as tests/_layout.py, same
    remedy: honor an explicit GOVERNANCE_ROOT env override (the reflex
    engine already passes one to sibling scripts), else walk up to the
    nearest ancestor containing `agentica_core/` — Governance/ in the nested
    layout, the pack root in the flat one.
    """
    env = os.environ.get("GOVERNANCE_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = (script_path or Path(__file__)).resolve()
    for ancestor in here.parents:
        if (ancestor / "agentica_core").is_dir():
            return ancestor
    return here.parents[2]  # historical nested-layout fallback


GOVERNANCE_ROOT = _resolve_governance_root()
DEFAULT_NPM_PROJECTS: tuple[tuple[str, Path], ...] = (
    ("Governance", GOVERNANCE_ROOT),
    ("Governance/api", GOVERNANCE_ROOT / "api"),
    ("Governance/dashboard-ui", GOVERNANCE_ROOT / "dashboard-ui"),
)

# Scanner subprocess timeout — remote index / advisory calls must never hang.
SCAN_TIMEOUT_S = 900  # pip list --outdated hits PyPI per package; 300s timed out weekly (launchd 2026-07-13)
NPM_SCAN_TIMEOUT_S = 180

# Licences that clear automatically. Anything outside this set (or empty/unknown)
# is flagged for the LLM/human judgement tail rather than auto-cleared.
PERMISSIVE_LICENCES = frozenset(
    {"mit", "mit-0", "mit-cmu", "bsd", "0bsd", "apache", "apache 2.0",
     "apache-2.0", "isc", "python", "python-2.0", "psf", "psfl", "psf-2.0",
     "cnri-python", "bsd-3-clause", "bsd-2-clause", "mpl-2.0", "unlicense",
     "zlib", "cc0-1.0"}
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
    Tolerant of empty / malformed output (returns []); run_audit separately
    marks malformed output unhealthy so this tolerant parser cannot create a
    false clean verdict.
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


def _valid_pip_outdated_output(stdout: str | None) -> bool:
    """Whether pip emitted its documented JSON-list envelope.

    An empty list is a valid clean verdict; malformed JSON is not. Keeping this
    separate from the tolerant parser prevents parse failure from becoming zero.
    """
    if stdout is None:
        return False
    try:
        return isinstance(json.loads(stdout), list)
    except (TypeError, ValueError):
        return False


def _valid_pip_audit_output(stdout: str | None) -> bool:
    """Whether pip-audit emitted one of its documented JSON envelopes."""
    if stdout is None:
        return False
    try:
        doc = json.loads(stdout)
    except (TypeError, ValueError):
        return False
    if isinstance(doc, list):
        return True
    return isinstance(doc, dict) and isinstance(doc.get("dependencies"), list)


def parse_npm_audit(stdout: str, project: str) -> dict | None:
    """Parse npm's audit-v2 JSON into the compact cross-project contract.

    Returns None unless the document contains the vulnerability metadata needed
    to distinguish a real zero from an npm/network/registry failure.
    """
    try:
        doc = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("error"):
        return None

    raw_counts = (doc.get("metadata") or {}).get("vulnerabilities")
    if not isinstance(raw_counts, dict):
        return None

    severities = ("info", "low", "moderate", "high", "critical")
    counts: dict[str, int] = {}
    for severity in severities:
        value = raw_counts.get(severity, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        counts[severity] = value
    raw_total = raw_counts.get("total", sum(counts.values()))
    if not isinstance(raw_total, int) or isinstance(raw_total, bool) or raw_total < 0:
        return None
    counts["total"] = raw_total

    findings: list[dict] = []
    raw_findings = doc.get("vulnerabilities") or {}
    if isinstance(raw_findings, dict):
        for package, finding in raw_findings.items():
            if not isinstance(finding, dict):
                continue
            vuln_ids: set[str] = set()
            for via in finding.get("via") or []:
                if not isinstance(via, dict):
                    continue
                url_tail = str(via.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
                source = via.get("source")
                if url_tail.startswith(("CVE-", "GHSA-")):
                    vuln_ids.add(url_tail)
                elif source is not None:
                    vuln_ids.add(f"npm:{source}")
            findings.append({
                "package": finding.get("name") or package,
                "severity": finding.get("severity") or "unknown",
                "vuln_ids": sorted(vuln_ids),
            })

    return {
        "project": project,
        "total": raw_total,
        "vulnerabilities": counts,
        "findings": sorted(findings, key=lambda row: str(row["package"]).lower()),
    }


def _is_permissive(low: str) -> bool:
    """True when a single (already-lowercased) licence term clears the allowlist.

    Any-token matching handles prose variants like "Modified BSD License" or
    "3-Clause BSD License"; it runs only after the copyleft check, so "GPL with
    BSD exception" can never reach it.
    """
    return low in PERMISSIVE_LICENCES or any(
        tok in PERMISSIVE_LICENCES for tok in low.split()
    )


def classify_licence(licence: str | None) -> str:
    """Classify a licence string as 'permissive', 'copyleft', or 'unknown'.

    Pure rule logic — the deterministic core of the licence scan. 'permissive'
    auto-clears; 'copyleft' and 'unknown' are surfaced for review. Accepts both
    prose ("MIT License") and PEP 639 SPDX expressions ("Apache-2.0 OR
    BSD-3-Clause"); a composite expression clears only if EVERY operand is
    permissive — OR is not trusted to pick the permissive branch.
    """
    if not licence or not licence.strip():
        return "unknown"
    low = licence.strip().lower()
    if any(marker in low for marker in COPYLEFT_MARKERS):
        return "copyleft"
    if " and " in low or " or " in low:
        parts = [
            p.strip()
            for p in re.split(r"\band\b|\bor\b", low.replace("(", " ").replace(")", " "))
            if p.strip()
        ]
        if parts and all(_is_permissive(p) for p in parts):
            return "permissive"
        return "unknown"
    if _is_permissive(low):
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
    npm_audits = npm_audits or []
    pip_vulnerability_count = 0
    for cve in pip_cves:
        count = cve.get("vuln_count", 1) if isinstance(cve, dict) else 1
        pip_vulnerability_count += (
            count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else 1
        )
    npm_vulnerability_count = sum(
        a.get("total", 0)
        for a in npm_audits
        if isinstance(a, dict) and isinstance(a.get("total", 0), int)
        and not isinstance(a.get("total", 0), bool)
    )
    needs_review = {
        "licences": [f for f in licence_flags if f["flag"] in ("copyleft", "unknown")],
        "cves": [c for c in pip_cves],  # every CVE wants a human/skill confirmation
    }
    return {
        "generated_at": generated_at,
        "pip_outdated": pip_outdated,
        "pip_cves": pip_cves,
        "npm_audits": npm_audits,
        "licence_flags": licence_flags,
        "needs_review": needs_review,
        "counts": {
            "outdated": len(pip_outdated),
            "cves": pip_vulnerability_count + npm_vulnerability_count,
            "pip_cves": pip_vulnerability_count,
            "npm_cves": npm_vulnerability_count,
            "licence_flags": len(licence_flags),
            "needs_review": len(needs_review["licences"]) + len(needs_review["cves"]),
        },
    }


# ---------------------------------------------------------------------------
# Real scanners
# ---------------------------------------------------------------------------

def _real_pip_outdated() -> str | None:
    """pip's outdated JSON, or None when pip itself failed — a dead pip would
    otherwise parse as "0 outdated" indefinitely with no failure marker."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--outdated", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _real_pip_audit() -> str | None:
    """Run pip-audit as a module. Returns its JSON stdout, or None when the
    scanner is dead (absent module, crash with no output).

    pip-audit exits non-zero when it finds vulnerabilities (that's success, not
    failure) — so a nonzero exit WITH stdout is a real result; nonzero with
    EMPTY stdout means the scanner never produced a verdict.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not proc.stdout:
        return None
    return proc.stdout


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
        # PEP 639: modern packages carry the SPDX expression and drop both the
        # legacy License field and the trove classifiers — read it first.
        licence = meta["License-Expression"] if "License-Expression" in meta else None
        if not licence:
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


def _find_npm() -> str | None:
    """Resolve npm under both an interactive shell and launchd's minimal PATH."""
    candidates = [
        shutil.which("npm"),
        str(Path.home() / ".local" / "share" / "mise" / "shims" / "npm"),
        str(Path.home() / ".local" / "share" / "mise" / "installs" / "node" / "latest" / "bin" / "npm"),
        "/opt/homebrew/bin/npm",
        "/usr/local/bin/npm",
        "/usr/bin/npm",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def scan_npm_projects(
    projects: Iterable[tuple[str, Path]] = DEFAULT_NPM_PROJECTS,
    *,
    npm_executable: str | None = None,
    run_fn: Callable[..., object] = subprocess.run,
) -> dict:
    """Run read-only npm audits for repo-owned lockfiles.

    npm exits 1 when vulnerabilities are found; that remains a successful scan.
    Exit codes above 1, malformed JSON, missing lockfiles, and timeouts are
    explicit per-project failures and never become zero findings.
    """
    project_rows = list(projects)
    npm_executable = npm_executable or _find_npm()
    audits: list[dict] = []
    project_status: dict[str, bool] = {}
    errors: dict[str, str] = {}

    if npm_executable is None:
        for label, _root in project_rows:
            project_status[label] = False
            errors[label] = "npm executable not found"
        return {"audits": audits, "scanner_ok": False,
                "projects": project_status, "errors": errors}

    for label, root in project_rows:
        if not (root / "package-lock.json").is_file():
            project_status[label] = False
            errors[label] = "package-lock.json missing"
            continue
        try:
            proc = run_fn(
                [npm_executable, "audit", "--json", "--package-lock-only", "--ignore-scripts"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=NPM_SCAN_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            project_status[label] = False
            errors[label] = f"{type(exc).__name__}: {exc}"[:300]
            continue

        returncode = getattr(proc, "returncode", 2)
        stdout = getattr(proc, "stdout", "") or ""
        parsed = parse_npm_audit(stdout, label)
        if returncode not in (0, 1) or parsed is None:
            stderr = (getattr(proc, "stderr", "") or "").strip()
            project_status[label] = False
            errors[label] = (stderr or f"npm audit returned {returncode} without usable JSON")[:300]
            continue
        audits.append(parsed)
        project_status[label] = True

    return {
        "audits": audits,
        "scanner_ok": bool(project_rows) and all(project_status.values()),
        "projects": project_status,
        "errors": errors,
    }


def _real_npm_audits() -> dict:
    return scan_npm_projects()


# ---------------------------------------------------------------------------
# Orchestration (testable via injected fns; no shell in tests)
# ---------------------------------------------------------------------------

def run_audit(
    *,
    pip_outdated_fn: Callable[[], str | None] = _real_pip_outdated,
    pip_audit_fn: Callable[[], str | None] = _real_pip_audit,
    licence_fn: Callable[[], list[tuple[str, str, str | None]]] = _installed_licences,
    npm_audit_fn: Callable[[], dict | list[dict]] | None = None,
    include_licences: bool = True,
    now_fn: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"),
) -> dict:
    """Run every scanner, classify, and assemble the audit dict.

    The npm scanner runs only when a hook is injected (CLI: --npm); the default
    None skips it without reporting a scanner failure — it queries the npm
    registry, so it stays opt-in.

    Pure given its injected scanner functions — the eval passes fixtures so it
    never shells out. Read-only: it scans and reports, never mutating any
    dependency, which is what makes re-running it inherently safe (idempotent).
    """
    pip_outdated_raw = pip_outdated_fn()
    pip_cves_raw = pip_audit_fn()
    pip_outdated = parse_pip_outdated(pip_outdated_raw or "")
    pip_cves = parse_pip_audit(pip_cves_raw or "")
    licence_flags = scan_licences(licence_fn()) if include_licences else []
    npm_result = npm_audit_fn() if npm_audit_fn is not None else None
    if npm_result is None:
        # npm scanning is opt-in (--npm / an injected hook): it queries the npm
        # registry, so a run without it deliberately skips the scan. A skipped
        # scanner is not a dead one — it contributes no scanner_ok/scanner_errors
        # entry, so consumers don't read the absence as a failed scan.
        npm_audits = []
        npm_ok = None
        npm_projects = {}
        npm_errors = {}
    elif isinstance(npm_result, dict):
        npm_audits = npm_result.get("audits")
        npm_audits = npm_audits if isinstance(npm_audits, list) else []
        npm_ok = npm_result.get("scanner_ok") is True
        npm_projects = npm_result.get("projects")
        npm_projects = npm_projects if isinstance(npm_projects, dict) else {}
        npm_errors = npm_result.get("errors")
        npm_errors = npm_errors if isinstance(npm_errors, dict) else {}
    elif isinstance(npm_result, list):
        # Compatibility for existing injected hooks. Production uses the explicit
        # result envelope above, including per-project health.
        npm_audits = npm_result
        npm_ok = True
        npm_projects = {"injected": True}
        npm_errors = {}
    else:
        npm_audits = []
        npm_ok = False
        npm_projects = {}
        npm_errors = {"npm": "scanner returned an invalid result envelope"}

    audit = build_audit(
        pip_outdated=pip_outdated,
        pip_cves=pip_cves,
        licence_flags=licence_flags,
        npm_audits=npm_audits,
        generated_at=now_fn(),
    )
    # Scanner health: lets consumers of dependency_audit.json distinguish a
    # genuinely clean scan from a dead scanner whose empty output zeroed the
    # counts (Deprecated_Deps would otherwise read healthy forever).
    audit["scanner_ok"] = {
        "pip": _valid_pip_outdated_output(pip_outdated_raw),
        "pip_audit": _valid_pip_audit_output(pip_cves_raw),
        **({} if npm_ok is None else {"npm": npm_ok}),
    }
    audit["scanner_details"] = {} if npm_ok is None else {"npm": npm_projects}
    audit["scanner_errors"] = {
        **({"pip": "pip outdated did not produce valid JSON"}
           if not audit["scanner_ok"]["pip"] else {}),
        **({"pip_audit": "pip-audit did not produce valid JSON"}
           if not audit["scanner_ok"]["pip_audit"] else {}),
        **({"npm": npm_errors or {"npm": "one or more npm audits failed"}}
           if npm_ok is False else {}),
    }
    return audit


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
    if audit["npm_audits"]:
        lines.append("\nNPM audits:")
        for npm_audit in audit["npm_audits"]:
            lines.append(
                f"  {npm_audit.get('project', '?')}  — "
                f"{npm_audit.get('total', '?')} vulnerabilities"
            )
    if audit["needs_review"]["licences"]:
        lines.append("\nLicences needing review:")
        for f in audit["needs_review"]["licences"]:
            lines.append(f"  [{f['flag']}] {f['name']} {f['version']}  — {f['licence']}")
    failed = [name for name, ok in (audit.get("scanner_ok") or {}).items() if ok is not True]
    if failed:
        lines.append(
            "\nSCANNER FAILURE: " + ", ".join(failed)
            + " — vulnerability/dependency counts are incomplete, not zero"
        )
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
    parser.add_argument("--npm", action="store_true",
                        help="also audit the repo-owned npm lockfiles (queries the "
                             "npm registry — a network call; off by default)")
    args = parser.parse_args(argv)

    audit = run_audit(
        include_licences=not args.no_licences,
        npm_audit_fn=_real_npm_audits if args.npm else None,
    )

    try:
        write_audit(audit, args.out)
    except OSError as exc:
        print(f"deps-audit: cannot write {args.out}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(audit, indent=2, sort_keys=True) if args.json
          else _format_report(audit, args.out))
    return 0 if all((audit.get("scanner_ok") or {}).values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
