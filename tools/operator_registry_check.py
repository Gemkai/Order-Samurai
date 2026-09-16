#!/usr/bin/env python3
"""operator_registry_check — validate the automation operator registry (P3 #9).

Consumer for Governance/config/operator_registry.json. Enforces the two gaps the
2026-07-07 loop-taxonomy audit found fleet-wide: every automation must declare an
owner (a human Champion) and a cost_cap_usd (cost governance). Also reports any
automation missing a checkable definition-of-done, and — when run on macOS — any
drift between the registry, the plist files in ~/Library/LaunchAgents, and the
jobs actually loaded in launchd (`launchctl list`, com.{agentica,claude}.*).

Live launchd state is the source of truth, not plist files. The 2026-07-26
adversarial review found two blind spots in the file-only scan (INVESTIGATE):
green-while-drift (a job bootstrapped from another path, or still loaded after
its plist was deleted, never trips a plist glob) and red-forever (a leftover
unloaded plist file blocks every push until deleted). So: a LOADED job that is
unregistered or has no plist is BLOCKING; a plist whose job is NOT loaded is a
warning only.

Exit code: 0 only if there are no BLOCKING gaps (missing owner or missing cost
cap) AND no blocking live drift. Unregistered-job drift became BLOCKING
2026-07-26 (audit P1-13): a live automation outside the registry is an
ungoverned owner-less cost, exactly the gap the registry exists to close.
Gate consumers: Governance/tests/test_operator_registry.py (registry
completeness in CI; live-job drift under @pytest.mark.live_machine via
verify.sh) and tools/self_audit.py.

Usage:
  python3 Governance/tools/operator_registry_check.py            # print report, exit 0/1
  python3 Governance/tools/operator_registry_check.py --selftest
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

# The directory this tool ships beside: Governance/ in the repo, the pack root in
# the Order Samurai public export (bin/extract_public.py allow-lists this file as
# tools/operator_registry_check.py). config/operator_registry.json sits one level
# up from tools/ in BOTH layouts, so the registry resolves from that directory --
# never from a fixed hop to the repo root, which in the export lands outside the
# distribution entirely.
GOVERNANCE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = GOVERNANCE_ROOT / "config" / "operator_registry.json"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

REQUIRED = ("id", "type", "owner", "cost_cap_usd", "has_checkable_dod")

# Owners governed BY Agentica-OS. Automations owned by another system (e.g. the
# Antigravity tree under ~/.gemini) run on this box but their cost/DoD governance
# belongs to that system, so they are reported informationally, not as blocking gaps.
SELF_OWNERS = {"gemkai", "agentica", "agentica-os"}

# The ONLY owners allowed to be treated as cross-system. Anything else — a typo
# like "gemaki", an empty-ish junk string — is a BLOCKING gap, not an exemption:
# otherwise a misspelled owner silently escapes every check while printing as
# "governed elsewhere".
KNOWN_CROSS_SYSTEM_OWNERS = {"antigravity"}


def load_registry(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("automations", [])


def check(automations: list[dict]) -> dict:
    """Return {blocking:[...], warnings:[...], cross_system:[...]}.

    Only Agentica-OS-owned automations can produce BLOCKING gaps; cross-system
    ones (owner not in SELF_OWNERS) are surfaced separately, not blocked on.
    """
    blocking, warnings, cross_system = [], [], []
    for a in automations:
        aid = a.get("id", "<no id>")
        owner = a.get("owner") or ""
        if owner in KNOWN_CROSS_SYSTEM_OWNERS:
            cross_system.append(f"{aid}: owner={owner} (governed elsewhere: {a.get('impl', 'external')})")
            continue
        if owner and owner not in SELF_OWNERS:
            blocking.append(f"{aid}: unknown owner {owner!r} (not a self owner or known cross-system owner)")
            continue
        if not owner:
            blocking.append(f"{aid}: missing owner (no accountable Champion)")
        if a.get("cost_cap_usd", None) is None:
            blocking.append(f"{aid}: missing cost_cap_usd (cost ungoverned)")
        if "auto_disable" in a and not isinstance(a["auto_disable"], bool):
            blocking.append(f"{aid}: auto_disable must be boolean (invalid safety policy)")
        if not a.get("has_checkable_dod"):
            warnings.append(f"{aid}: no checkable definition-of-done")
    return {"blocking": blocking, "warnings": warnings, "cross_system": cross_system}


def plist_labels() -> set[str]:
    if not LAUNCH_AGENTS.is_dir():
        return set()
    return {p.stem for p in LAUNCH_AGENTS.glob("com.*.plist")
            if p.stem.startswith(("com.agentica", "com.claude"))}


# Hardcoded so a PATH shim (mise/direnv/homebrew shadowing) can never stand in
# for the gate's source of truth; missing binary falls through to None.
LAUNCHCTL = "/bin/launchctl"


def _parse_launchctl(text: str) -> set[str]:
    """Parse `launchctl list` output ("PID\tStatus\tLabel" rows, header
    included) into the com.agentica.*/com.claude.* label set."""
    labels = set()
    for line in text.splitlines():
        label = line.rsplit("\t", 1)[-1].strip()
        if label.startswith(("com.agentica", "com.claude")):
            labels.add(label)
    return labels


def launchctl_labels() -> set[str] | None:
    """Labels actually loaded in launchd (com.agentica.*/com.claude.*), or None
    when launchctl is unavailable (non-macOS host, CI). None means "live state
    unknown" — callers must fall back to the file-only check, never treat it as
    "nothing loaded"."""
    try:
        proc = subprocess.run([LAUNCHCTL, "list"], capture_output=True,
                              text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_launchctl(proc.stdout)


def classify_drift(registered_ids: set[str], plists: set[str],
                   loaded: set[str] | None) -> dict:
    """Return {blocking:[...], warnings:[...]} from the three-way cross-check.

    BLOCKING — a LOADED job that is unregistered (running outside governance)
    or has no plist in ~/Library/LaunchAgents (survives plist deletion, or was
    bootstrapped from another path).
    WARNING — a plist file whose job is not loaded: stale intent, not a live
    automation; must not block pushes.
    loaded=None (launchctl unavailable) falls back to the legacy file-only
    check: an unregistered plist is blocking, loaded-state findings are skipped.
    """
    blocking, warnings = [], []
    if loaded is None:
        for label in sorted(plists - registered_ids):
            blocking.append(f"{label}: plist has no registry entry (launchctl unavailable, file-only check)")
        return {"blocking": blocking, "warnings": warnings}
    for label in sorted(loaded):
        reasons = []
        if label not in registered_ids:
            reasons.append("no registry entry")
        if label not in plists:
            reasons.append("no plist in ~/Library/LaunchAgents (bootstrapped elsewhere or plist deleted)")
        if reasons:
            blocking.append(f"{label}: loaded in launchd but " + " and ".join(reasons))
    for label in sorted(plists - loaded):
        suffix = "" if label in registered_ids else " (also unregistered)"
        warnings.append(f"{label}: plist present but job not loaded — load it or delete the file{suffix}")
    return {"blocking": blocking, "warnings": warnings}


def find_drift(registered_ids: set[str]) -> dict:
    return classify_drift(registered_ids, plist_labels(), launchctl_labels())


def find_unregistered(registered_ids: set[str]) -> list[str]:
    """Back-compat surface for self_audit.py (and the in-flight
    agentica_core/scorecard.py branch): the BLOCKING drift findings
    ("label: reason" strings)."""
    return find_drift(registered_ids)["blocking"]


def _declaration_file(root: Path, relative: str, label: str, errors: list[str]):
    """Inspect only a contained file; a rejected path has unknown presence."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        errors.append(f"{label}: path must be relative and contained")
        return None, None
    try:
        candidate = (root / path).resolve()
        candidate.relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError):
        errors.append(f"{label}: path escapes root or cannot be resolved")
        return None, None
    try:
        present = stat.S_ISREG(candidate.stat().st_mode)
    except FileNotFoundError:
        present = False
    except OSError as exc:
        errors.append(f"{label}: file observation unavailable ({type(exc).__name__})")
        return str(candidate), None
    return str(candidate), present


def audit_legacy_declarations(manifest_path, scripts_root, launch_agents,
                              registered_ids, loaded) -> dict:
    """Read-only declaration facts, never a health or replacement verdict.

    Callers supply the correct script namespace and an exact observed label set.
    None means unavailable live observation. Notes are preserved as source text.
    No declared command is executed and no scheduler/configuration is modified.
    """
    report = {"rows": [], "errors": [], "source_sha256": None}
    errors = report["errors"]
    try:
        with Path(manifest_path).open("rb") as stream:
            raw = stream.read(1_048_577)
    except OSError as exc:
        errors.append(f"manifest unreadable ({type(exc).__name__})")
        return report
    if len(raw) > 1_048_576:
        errors.append("manifest exceeds 1 MiB observation limit")
        return report
    report["source_sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeError):
        errors.append("manifest is not valid JSON")
        return report
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        errors.append("manifest needs a tasks array")
        return report
    tasks = manifest["tasks"]
    counts = Counter(t.get("launchd_id") for t in tasks
                     if isinstance(t, dict) and isinstance(t.get("launchd_id"), str))
    duplicate_labels = {label for label, count in counts.items() if count > 1}
    errors.extend(f"duplicate label: {label}" for label in sorted(duplicate_labels))
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"row {index}: declaration must be an object")
            continue
        label, name, script = (task.get(k) for k in ("launchd_id", "name", "script"))
        if not isinstance(label, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", label
        ):
            errors.append(f"row {index}: invalid launchd label")
            continue
        if label in duplicate_labels:
            continue
        if not isinstance(name, str) or not name.strip() or not isinstance(script, str) or not script:
            errors.append(f"{label}: declaration needs a name and script")
            continue
        if "\x00" in script:
            errors.append(f"{label}: invalid script path")
            continue
        script_path, script_exists = _declaration_file(
            Path(scripts_root), script, label, errors,
        )
        plist_path, plist_exists = _declaration_file(
            Path(launch_agents), f"{label}.plist", label, errors,
        )
        report["rows"].append({
            "name": name, "label": label,
            "script_path": script_path, "script_exists": script_exists,
            "plist_path": plist_path, "plist_exists": plist_exists,
            "registered": label in registered_ids,
            "loaded": None if loaded is None else label in loaded,
            "health": "not_evaluated", "note": task.get("_note"),
        })
    return report


def _legacy_declaration_report(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Read-only legacy declaration reconciliation")
    parser.add_argument("--legacy-manifest", type=Path, required=True)
    parser.add_argument("--scripts-root", type=Path, required=True,
                        help="Script root for this manifest's namespace; split mixed namespaces first")
    parser.add_argument("--launch-agents", type=Path, default=LAUNCH_AGENTS)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    args = parser.parse_args(argv)
    try:
        automations = load_registry(args.registry)
        if not isinstance(automations, list) or any(
            not isinstance(a, dict) or not isinstance(a.get("id"), str) for a in automations
        ):
            raise ValueError("operator registry needs automation IDs")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(json.dumps({"rows": [], "errors": [f"operator registry unavailable ({type(exc).__name__})"],
                          "source_sha256": None}))
        return 2
    loaded = launchctl_labels()
    report = audit_legacy_declarations(
        args.legacy_manifest, args.scripts_root, args.launch_agents,
        {a["id"] for a in automations}, loaded,
    )
    report["loaded_observation_available"] = loaded is not None
    report["interpretation"] = "Declaration facts only; no execution health or replacement equivalence evaluated."
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if report["errors"] else 0


def _selftest() -> int:
    good = [{"id": "x", "type": "time", "owner": "gemkai", "cost_cap_usd": 0.0, "has_checkable_dod": True}]
    assert check(good)["blocking"] == [], "complete self-owned entry should not block"
    bad = [{"id": "y", "type": "time", "owner": "", "cost_cap_usd": None, "has_checkable_dod": False}]
    r = check(bad)
    assert len(r["blocking"]) == 2 and len(r["warnings"]) == 1, r
    cross = [{"id": "z", "type": "time", "owner": "antigravity", "cost_cap_usd": None, "has_checkable_dod": False}]
    rc = check(cross)
    assert rc["blocking"] == [] and len(rc["cross_system"]) == 1, "cross-system must not block"
    typo = [{"id": "t", "type": "time", "owner": "gemaki", "cost_cap_usd": None, "has_checkable_dod": False}]
    rt = check(typo)
    assert len(rt["blocking"]) == 1 and rt["cross_system"] == [], "unknown owner must block, not exempt"
    job = "com.agentica.t"
    d = classify_drift(set(), plists={job}, loaded={job})
    assert len(d["blocking"]) == 1, "loaded-but-unregistered must block"
    d = classify_drift({job}, plists=set(), loaded={job})
    assert len(d["blocking"]) == 1, "loaded-but-no-plist must block even when registered"
    d = classify_drift(set(), plists={job}, loaded=set())
    assert d["blocking"] == [] and len(d["warnings"]) == 1, "unloaded leftover plist must warn, not block"
    d = classify_drift({job}, plists={job}, loaded={job})
    assert d["blocking"] == [] and d["warnings"] == [], "registered+plist+loaded is clean"
    d = classify_drift(set(), plists={job}, loaded=None)
    assert len(d["blocking"]) == 1, "file-only fallback must still block unregistered plists"
    print("selftest OK")
    return 0


def main() -> int:
    if "--legacy-manifest" in sys.argv:
        return _legacy_declaration_report(sys.argv[1:])
    if "--selftest" in sys.argv:
        return _selftest()
    automations = load_registry(REGISTRY)
    r = check(automations)
    drift = find_drift({a.get("id", "") for a in automations})

    print(f"Operator registry: {len(automations)} automations")
    print(f"  BLOCKING gaps (Agentica-OS-owned, missing owner or cost cap): {len(r['blocking'])}")
    for b in r["blocking"]:
        print(f"    ✗ {b}")
    print(f"  Warnings (no checkable DoD): {len(r['warnings'])}")
    for w in r["warnings"]:
        print(f"    ! {w}")
    print(f"  Cross-system (governed elsewhere, informational): {len(r['cross_system'])}")
    for c in r["cross_system"]:
        print(f"    ~ {c}")
    if drift["blocking"]:
        print(f"  BLOCKING drift — live launchd state vs registry/plists ({len(drift['blocking'])}):")
        for u in drift["blocking"]:
            print(f"    ✗ {u}")
        print("  Register each in Governance/config/operator_registry.json (owner + cost_cap_usd), or restore its plist.")
    else:
        print("  Blocking launchd drift: 0 (or non-macOS host)")
    if drift["warnings"]:
        print(f"  Drift warnings (plist present, job not loaded): {len(drift['warnings'])}")
        for w in drift["warnings"]:
            print(f"    ! {w}")

    # Cost cap is intentionally allowed to be 0.0 (deterministic, no LLM). Only
    # None (undeclared) blocks — an undeclared cost is an ungoverned cost.
    return 1 if (r["blocking"] or drift["blocking"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
