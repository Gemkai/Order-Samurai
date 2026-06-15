#!/usr/bin/env python3
"""Deterministic pip-safe-upgrade mechanism.

The mechanical core of the /pip-safe-upgrade skill, extracted as a deterministic,
testable mechanism (RONIN-DETERMINIZATION-PLAN.md, candidate #1). The skill's
judgement-free 90% — triage packages, detect ML constraints, parse dry-run output,
decide apply-vs-block — is pure rule logic, so it runs faster and ships with a real
eval (tests/test_pip_safe_upgrade.py) instead of a 0%-success LLM remediation.

What stays LLM: the genuinely ambiguous tail (novel constraint conflicts the rules
block but a human could resolve). Those surface in the report as `blocked` with a
reason, for a human or the /pip-safe-upgrade skill to judge.

Usage:
    python bin/pip_safe_upgrade.py [--audit PATH] [--apply] [--json]

Default is plan-only (no side effects): reads the dependency audit, produces the
deterministic upgrade plan, prints the report. `--apply` actually runs pip on the
packages the rules cleared.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

# Default audit location (written by dependency_audit.py / codebase-cleanup-deps-audit).
DEFAULT_AUDIT_PATH = Path.home() / ".claude" / "data" / "dependency_audit.json"

# Security-adjacent packages: upgraded right after CVE packages, ahead of the rest.
# Lowercased for case-insensitive matching against audit names.
SECURITY_ADJACENT = frozenset(
    {"certifi", "cryptography", "urllib3", "requests", "pyopenssl", "idna"}
)

# Packages whose presence flips the mechanism into ML-constraint mode.
ML_MARKERS = frozenset({"torch", "transformers", "sentence-transformers"})

# In ML mode, setuptools must stay below this major (torch pins setuptools<82).
SETUPTOOLS_ML_CEILING = 82

# pip subprocess timeout — remote index calls must never hang the mechanism.
PIP_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def _version_key(version: str) -> tuple[int, ...]:
    """Comparable key from the leading numeric components of a version string.

    Tolerant of suffixes (rc, post, +cu121): "82.0.1" -> (82, 0, 1),
    "2.12.0+cu121" -> (2, 12, 0). Non-numeric versions compare as (-1,)
    so they never falsely register as a downgrade.
    """
    parts: list[int] = []
    for token in re.split(r"[.\-+_]", version.strip()):
        m = re.match(r"^(\d+)", token)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts) if parts else (-1,)


def _version_lt(a: str, b: str) -> bool:
    """True if version a is strictly older than version b."""
    return _version_key(a) < _version_key(b)


def _major(version: str) -> int:
    """Leading major-version integer, or -1 if unparseable."""
    return _version_key(version)[0]


# ---------------------------------------------------------------------------
# Triage (pure)
# ---------------------------------------------------------------------------

class Candidate:
    """One package the mechanism may upgrade, with its risk tier and versions."""

    __slots__ = ("name", "current", "target", "tier")

    def __init__(self, name: str, current: str, target: str, tier: str) -> None:
        self.name = name
        self.current = current
        self.target = target
        self.tier = tier  # "cve" | "security" | "rest"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Candidate) and (
            self.name,
            self.current,
            self.target,
            self.tier,
        ) == (other.name, other.current, other.target, other.tier)

    def __repr__(self) -> str:
        return f"Candidate({self.name} {self.current}->{self.target} [{self.tier}])"


def triage(audit: dict) -> list[Candidate]:
    """Order outdated packages by risk: CVE first, then security-adjacent, then rest.

    Each package appears once. CVE membership wins over security/rest; security wins
    over rest. Packages in pip_cves but absent from pip_outdated still upgrade (their
    target is the audit's latest if known, else "latest"). Deterministic ordering:
    within each tier, alphabetical by name.
    """
    outdated = {p["name"].lower(): p for p in audit.get("pip_outdated", [])}
    cve_names = {c["package"].lower() for c in audit.get("pip_cves", [])}

    candidates: list[Candidate] = []
    seen: set[str] = set()

    def add(name_lower: str, tier: str) -> None:
        if name_lower in seen:
            return
        seen.add(name_lower)
        info = outdated.get(name_lower, {})
        candidates.append(
            Candidate(
                name=info.get("name", name_lower),
                current=info.get("version", "unknown"),
                target=info.get("latest", "latest"),
                tier=tier,
            )
        )

    for name in sorted(cve_names):
        add(name, "cve")
    for name in sorted(n for n in outdated if n in SECURITY_ADJACENT):
        add(name, "security")
    for name in sorted(outdated):
        add(name, "rest")

    return candidates


# ---------------------------------------------------------------------------
# ML constraint detection (pure)
# ---------------------------------------------------------------------------

def detect_ml_mode(installed: set[str]) -> bool:
    """True if any ML marker package is installed (triggers ML constraint mode)."""
    return bool(ML_MARKERS & {p.lower() for p in installed})


# ---------------------------------------------------------------------------
# Dry-run parsing (pure)
# ---------------------------------------------------------------------------

# Matches "name-version" tokens like "setuptools-65.5.0" or "torch-2.12.0+cu121".
_PKG_TOKEN_RE = re.compile(r"([A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*)-(\d[\w.+]*)")


def _extract_pkgs(line: str) -> list[tuple[str, str]]:
    """Extract (name, version) pairs from a pip 'Would install/uninstall' line."""
    payload = re.split(r"[Ww]ould (?:install|uninstall):?", line, maxsplit=1)[-1]
    return [(m.group(1), m.group(2)) for m in _PKG_TOKEN_RE.finditer(payload)]


def parse_dry_run(output: str) -> dict:
    """Parse `pip install --upgrade --dry-run` output into a structured verdict.

    Returns {"would_install": [(name, ver)], "would_uninstall": [(name, ver)],
    "incompatible": bool}. Incompatible is set when pip reports a dependency
    conflict / incompatibility (the dry-run red flag from the skill).
    """
    would_install: list[tuple[str, str]] = []
    would_uninstall: list[tuple[str, str]] = []
    incompatible = False
    for line in output.splitlines():
        low = line.lower()
        if "would install" in low:
            would_install.extend(_extract_pkgs(line))
        if "would uninstall" in low:
            would_uninstall.extend(_extract_pkgs(line))
        if "incompatible" in low or "dependency conflict" in low:
            incompatible = True
    return {
        "would_install": would_install,
        "would_uninstall": would_uninstall,
        "incompatible": incompatible,
    }


def detect_downgrades(parsed: dict) -> list[tuple[str, str, str]]:
    """Packages the dry-run would replace with an OLDER version (constraint-forced).

    Returns [(name, from_version, to_version)]. A downgrade is the skill's primary
    red flag: pip found a constraint that pulls a package backwards.
    """
    uninstall = {name.lower(): ver for name, ver in parsed.get("would_uninstall", [])}
    downgrades: list[tuple[str, str, str]] = []
    for name, install_ver in parsed.get("would_install", []):
        from_ver = uninstall.get(name.lower())
        if from_ver is not None and _version_lt(install_ver, from_ver):
            downgrades.append((name, from_ver, install_ver))
    return downgrades


# ---------------------------------------------------------------------------
# Decision (pure)
# ---------------------------------------------------------------------------

def already_current(candidate: Candidate) -> bool:
    """True if the candidate is already at its target version (a no-op upgrade)."""
    return candidate.current != "unknown" and candidate.current == candidate.target


def ml_hard_block(candidate: Candidate) -> str | None:
    """Return a block reason if an ML-mode pin forbids this upgrade, else None.

    These rules pre-empt the dry-run entirely — they hold regardless of what pip
    would do, because the constraint is the ML framework's pin, not a transient
    resolver outcome.
    """
    name = candidate.name.lower()
    if name == "torch":
        return "ML mode: torch upgrade needs a manual compatibility matrix"
    if name == "setuptools" and _major(candidate.target) >= SETUPTOOLS_ML_CEILING:
        return (
            f"ML mode: setuptools>={SETUPTOOLS_ML_CEILING} conflicts with "
            f"torch pin (<{SETUPTOOLS_ML_CEILING})"
        )
    return None


def decide(candidate: Candidate, parsed: dict | None, ml_mode: bool) -> tuple[str, str]:
    """Decide what to do with one candidate. Returns (action, reason).

    action ∈ {"apply", "block", "skip"}:
      - skip  : already at target (no-op; keeps the mechanism idempotent)
      - block : a deterministic rule forbids the upgrade (ML pin, downgrade, conflict)
      - apply : dry-run is clean; safe to upgrade

    `parsed` is the dry-run verdict, or None for ML hard-blocks decided before any
    dry-run is needed.
    """
    if already_current(candidate):
        return ("skip", "already at target version")

    if ml_mode:
        block = ml_hard_block(candidate)
        if block:
            return ("block", block)

    if parsed is None:
        # No dry-run available and no hard rule fired — cannot clear it deterministically.
        return ("block", "no dry-run result to evaluate")

    if parsed.get("incompatible"):
        return ("block", "dry-run reported an incompatibility")

    downgrades = detect_downgrades(parsed)
    if downgrades:
        name, frm, to = downgrades[0]
        return ("block", f"dry-run would downgrade {name} {frm}->{to} (constraint conflict)")

    return ("apply", "dry-run clean")


# ---------------------------------------------------------------------------
# Orchestration (testable via injected fns; no pip side effects in tests)
# ---------------------------------------------------------------------------

def _real_dry_run(name: str) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", name, "--dry-run"],
        capture_output=True,
        text=True,
        timeout=PIP_TIMEOUT_S,
    )
    return proc.stdout + proc.stderr


def _real_apply(name: str) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", name],
        capture_output=True,
        text=True,
        timeout=PIP_TIMEOUT_S,
    )
    return proc.returncode == 0


def run_plan(
    audit: dict,
    installed: set[str],
    *,
    do_apply: bool = False,
    dry_run_fn: Callable[[str], str] = _real_dry_run,
    apply_fn: Callable[[str], bool] = _real_apply,
) -> dict:
    """Build and (optionally) execute the deterministic upgrade plan.

    Pure given its injected `dry_run_fn` / `apply_fn` — the unit test passes fixtures
    so it never shells out to pip. Returns a structured, JSON-serialisable report.
    """
    ml_mode = detect_ml_mode(installed)
    candidates = triage(audit)

    applied: list[dict] = []
    blocked: list[dict] = []
    skipped: list[dict] = []

    for cand in candidates:
        # Skip and ML hard-block are decided without a dry-run — never shell out to
        # pip for a package the rules already resolve (keeps re-runs free + idempotent).
        if already_current(cand):
            skipped.append(_row(cand, "already at target version"))
            continue
        if ml_mode:
            block = ml_hard_block(cand)
            if block:
                blocked.append(_row(cand, block))
                continue

        parsed = parse_dry_run(dry_run_fn(cand.name))
        action, reason = decide(cand, parsed, ml_mode)

        if action == "skip":
            skipped.append(_row(cand, reason))
        elif action == "block":
            blocked.append(_row(cand, reason))
        else:  # apply
            row = _row(cand, reason)
            if do_apply:
                row["upgraded"] = apply_fn(cand.name)
            applied.append(row)

    return {
        "ml_mode": ml_mode,
        "applied": applied,
        "blocked": blocked,
        "skipped": skipped,
        "counts": {
            "candidates": len(candidates),
            "applied": len(applied),
            "blocked": len(blocked),
            "skipped": len(skipped),
        },
    }


def _row(cand: Candidate, reason: str) -> dict:
    return {
        "name": cand.name,
        "current": cand.current,
        "target": cand.target,
        "tier": cand.tier,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _installed_packages() -> set[str]:
    try:
        from importlib import metadata
        return {d.metadata["Name"].lower() for d in metadata.distributions() if d.metadata["Name"]}
    except Exception:
        return set()


def _format_report(report: dict) -> str:
    lines = [f"ML constraint mode: {'ON' if report['ml_mode'] else 'off'}"]
    for bucket, verb in (("applied", "APPLY"), ("blocked", "BLOCK"), ("skipped", "SKIP")):
        rows = report[bucket]
        if not rows:
            continue
        lines.append(f"\n{verb} ({len(rows)}):")
        for r in rows:
            lines.append(f"  [{r['tier']}] {r['name']} {r['current']}->{r['target']}  — {r['reason']}")
    c = report["counts"]
    lines.append(
        f"\nTotal: {c['candidates']} candidates · {c['applied']} apply · "
        f"{c['blocked']} block · {c['skipped']} skip"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic pip-safe-upgrade mechanism")
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT_PATH,
                        help="path to dependency_audit.json")
    parser.add_argument("--apply", action="store_true",
                        help="actually upgrade rule-cleared packages (default: plan only)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"pip-safe-upgrade: cannot read audit {args.audit}: {exc}", file=sys.stderr)
        return 1

    report = run_plan(audit, _installed_packages(), do_apply=args.apply)
    print(json.dumps(report, indent=2) if args.json else _format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
