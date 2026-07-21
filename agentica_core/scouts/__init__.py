"""System scouts — real local signals not derivable from telemetry. Honest by construction:
each returns a concrete count or None (never a fabricated value).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _count_lines(path: Path) -> int | None:
    """Count valid JSON objects in a JSONL file (one per line).

    Using JSON-parse count rather than raw line count prevents overcounting
    when files are ever written with multi-line pretty-printing.
    """
    import json as _json
    try:
        count = 0
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    _json.loads(ln)
                    count += 1
                except _json.JSONDecodeError:
                    pass  # skip malformed or continuation lines
        return count
    except OSError:
        return None


def security_signals(runtime_root: Path, platform: str | None = None) -> dict:
    """Read the security telemetry a platform's hooks ALREADY emit (under <runtime_root>/data).
    Reads existing logs — does not touch the security hooks. Missing files are simply omitted."""
    data = runtime_root / "data"
    out: dict[str, int] = {}

    # rule_violations removed from scout: now a per-session DERIVED metric emitted by
    # agentica_emit.py, enabling tier/project breakdown. See aggregate.py REGISTRY.

    sg = data / "security_gate_log.jsonl"
    if sg.exists():
        fires = 0
        for ln in sg.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("findings") or rec.get("finding_count") or rec.get("exit_code"):
                fires += 1
        out["gate_fires"] = fires

    dep = _read_json(data / "dependency_audit.json")
    if isinstance(dep, dict):
        cves = dep.get("pip_cves") or []
        n = len(cves) if isinstance(cves, list) else 0
        npm = dep.get("npm_audits")
        if isinstance(npm, list):
            n += sum(a.get("total", 0) for a in npm if isinstance(a, dict))
        elif isinstance(npm, dict):
            n += sum(len(v) for v in npm.values() if isinstance(v, list))
        out["open_cves"] = n
        out["deprecated_deps"] = len(dep.get("pip_outdated") or [])

    can = _read_json(data / "canary_status.json")
    if isinstance(can, dict) and "failed" in can:
        # A run where every canary failed to even execute (harness/spawn fault — e.g. the
        # `claude --print` child crashing on init, not a skill verdict) is not evidence that
        # skills are healthy OR broken. Leave the metric SIMULATED (honest unknown) rather than
        # asserting a false all-clear or all-fail. behavioral_canary.py reports could_not_run +
        # total; older snapshots omit them, so only suppress when both are present and conclusive.
        total = can.get("total")
        cnr = can.get("could_not_run")
        all_harness_fault = (
            isinstance(cnr, int) and isinstance(total, int) and total > 0 and cnr >= total
        )
        if not all_harness_fault:
            out["canary_failures"] = int(can.get("failed") or 0)

    # Sword: security-gate self-test. Fault if the gate failed its last canary OR the canary
    # is older than its own freshness budget (a stale all-clear is not an all-clear).
    gc = _read_json(data / "security_gate_canary.json")
    if isinstance(gc, dict) and "gate_working" in gc:
        fault = 0 if gc.get("gate_working") else 1
        last, max_age = gc.get("last_run"), gc.get("max_age_days")
        if not fault and last and isinstance(max_age, (int, float)):
            try:
                t = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - t).days > max_age:
                    fault = 1
            except ValueError:
                pass
        out["gate_canary_fault"] = fault

    # loop_breaker_fires RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # loop_breaker_state.json is never written on this host — the emitter never
    # fired and the graded metric was permanently dark. Removal, never faking.

    ma = _read_json(data / "mechanism_audit.json")
    if isinstance(ma, dict):
        c = ma.get("counts") or {}
        out["mechanism_orphans"] = int(c.get("orphan", 0)) + int(c.get("critical", 0))

    dp = _read_json(data / "doc_parity.json")
    if isinstance(dp, dict):
        out["doc_parity_issues"] = (len(dp.get("broken_refs") or [])
                                    + len(dp.get("undocumented_hooks") or [])
                                    + len(dp.get("unwired_hooks") or []))

    # ARTS-001: live documentation-parity scout (Order Samurai repo)
    try:
        from agentica_core.scouts.doc_parity import run as _dp
        dp_live = _dp()
        out["doc_parity_issues"] = dp_live.get("doc_parity_issues", 0)
    except Exception:
        pass

    reaped = _count_lines(data / "mcp_reaper.jsonl")
    if reaped is not None:
        out["processes_reaped"] = reaped

    # Sword: weighted security scorecard (this platform's own total, 0-100; non-additive)
    sc = _read_json(data / "security_scorecard.json")
    if isinstance(sc, dict) and platform:
        plat = (sc.get("platforms") or {}).get(platform)
        if isinstance(plat, dict) and "total" in plat:
            out["security_scorecard"] = plat["total"]

    # Sword: red-team-style safety scan of installed skills (supply-chain vetting)
    ss = _read_json(data / "skill_safety_scan.json")
    if isinstance(ss, dict):
        out["skill_safety_findings"] = int(ss.get("critical_count", 0)) + int(ss.get("warning_count", 0))

    # skills_optimized + skill_promotions RETIRED 2026-07-19 (metric-surface review
    # Part E item 3): skill_improve_after_use_log.jsonl / skill_promotion_log.jsonl
    # are never written on this host — both counters were permanently dark.

    # Arts: craft signals — skill conflicts
    conf = _read_json(data / "skill_conflicts.json")
    if isinstance(conf, dict):
        out["skill_conflicts"] = len(conf.get("groups") or [])

    # secret_scrubs RETIRED 2026-07-19 (metric-surface review Part E item 3):
    # secret_scrubber.jsonl is absent on this host — the protective counter never
    # fired. Secrets_Detected (secret_scrub.py mechanism) is the live secrets metric.

    # Bow: MCP connectivity smoke-test failures
    sm = _read_json(data / "mcp_smoke_test.json")
    if isinstance(sm, dict) and "fail_count" in sm:
        out["mcp_smoke_fails"] = int(sm["fail_count"])

    # SWORD-001 guardrail_blocks RETIRED 2026-07-19: security_gate_log.jsonl has no
    # writer on this host (Windows-era gate log) — re-introduce only with a real
    # block-logger in the live guardrails hook.

    # SWORD-002: security scorecard total
    sc2 = score_security_posture(runtime_root)
    if sc2 is not None:
        out["security_scorecard_total"] = sc2

    # GOVERNANCE-001: adversarial governance code review findings (CRITICAL+HIGH count)
    gov = governance_findings()
    if gov:
        out.update(gov)

    # AUTO-001: Config Drift Rate — weekly count of config-file changes (added/changed/removed)
    drift_log = data / "config_integrity_drift.jsonl"
    if drift_log.exists():
        this_week = datetime.now(timezone.utc).strftime("%G-W%V")
        drift_count = 0
        for ln in drift_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
                ts = obj.get("ts", "")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if dt.strftime("%G-W%V") == this_week:
                        drift_count += 1
            except Exception:
                continue
        out["config_drift_rate"] = drift_count

    # AUTO-007: Vulnerability Window (Patch Latency) — days since CVEs were first detected
    dep_window = _read_json(data / "dependency_audit.json")
    if isinstance(dep_window, dict):
        cves_w = dep_window.get("pip_cves") or []
        npm_w = dep_window.get("npm_audits")
        n_cves_w = len(cves_w) if isinstance(cves_w, list) else 0
        if isinstance(npm_w, list):
            n_cves_w += sum(a.get("total", 0) for a in npm_w if isinstance(a, dict))
        if n_cves_w > 0:
            gen_at = dep_window.get("generated_at")
            if gen_at:
                try:
                    dt_gen = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
                    if dt_gen.tzinfo is None:
                        dt_gen = dt_gen.replace(tzinfo=timezone.utc)
                    out["vulnerability_window_days"] = round(
                        (datetime.now(timezone.utc) - dt_gen).total_seconds() / 86400, 1
                    )
                except Exception:
                    pass

    # SWORD-kill_chain_discovery: discover untracked kill chains from telemetry
    try:
        from agentica_core.scouts.kill_chain_discovery import run as _kcd
        kcd = _kcd(runtime_root)
        out["kill_chain_candidates"] = kcd.get("kill_chain_candidates", 0)
    except Exception:
        pass

    return out


def score_security_posture(runtime_root: Path, platform: str = "claude") -> int | float | None:
    """SWORD-002: return the weighted security scorecard total for *platform* (0-100).

    Reads security_scorecard.json for platforms.<platform>.total.
    If security_gate_canary.json reports gate_working == False, subtracts 20 from the score.
    Returns None if the scorecard file is missing or the platform key is absent.
    """
    data = runtime_root / "data"
    sc = _read_json(data / "security_scorecard.json")
    if not isinstance(sc, dict):
        return None
    plat = (sc.get("platforms") or {}).get(platform)
    if not isinstance(plat, dict) or "total" not in plat:
        return None
    total = plat["total"]
    gc = _read_json(data / "security_gate_canary.json")
    if isinstance(gc, dict) and gc.get("gate_working") is False:
        total = total - 20
    return total


def governance_findings() -> dict:
    """GOVERNANCE-001: read governance_findings.json produced by governance_review.py.

    Returns CRITICAL+HIGH finding counts, or empty dict if the file is absent or malformed.
    Path is derived from this file's location so it works regardless of cwd.
    """
    gov_root = Path(__file__).resolve().parent.parent.parent  # scouts/ → agentica_core/ → Governance/
    path = gov_root / "docs" / "governance_findings.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return {}
    totals = data.get("total", {})
    critical = int(totals.get("CRITICAL", 0) or 0)
    high = int(totals.get("HIGH", 0) or 0)
    return {
        "governance_findings_critical": critical,
        "governance_findings_high": high,
        "governance_findings_total_ch": critical + high,
    }


# Maps each architecture-scorecard category id to keywords matched against verifier FAIL labels:
# a category loses its weight only if a verifier whose label contains one of its keywords FAILed.
# (Recovered from commit 9584c5a — the definition was dropped, leaving score_architecture raising
# NameError, which broke Architecture_Scorecard_Grade and ~9 aggregate tests.)
_SCORECARD_KW = {
    "path_authority": ["path-authority", "hardcoded"],
    "runtime_coherence": ["runtime"],
    "truth_separation": ["generated_truth", "truth", "runtime-contract"],
    "surface_governance": ["surface"],
    "root_hygiene": ["hygiene"],
    "archive_isolation": ["archive", "boundary"],
    "lifecycle_governance": ["promotion", "lifecycle"],
    "documentation_parity": ["doc", "parity"],
}


def score_architecture(verifier_results: list[dict], scorecard_path: Path) -> float | None:
    """Real weighted grade: award each scorecard category its full weight unless a verifier
    FAIL maps to it. Computed from actual verifier results + the declared category weights."""
    sc = _read_json(scorecard_path)
    if not isinstance(sc, dict):
        return None
    cats = sc.get("categories") or []
    if not cats:
        return None
    fails = [r for r in verifier_results if r.get("status") == "FAIL"]
    score = 0
    for c in cats:
        kws = _SCORECARD_KW.get(c.get("id"), [])
        failed = any(any(k in r.get("label", "").lower() for k in kws) for r in fails)
        if not failed:
            score += c.get("weight", 0)
    return round(float(score), 1)


def agent_process_count() -> int | None:
    """Current python/node process footprint (the live agent runtime). Real and conservative.

    This is NOT 'zombies killed' — that requires kill events in autonomic_events.jsonl. This is an
    honest point-in-time process count. Returns None if the host can't be queried.
    """
    try:
        import psutil  # optional; precise if present
        return sum(
            1 for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower().startswith(("python", "node"))
        )
    except Exception:
        pass
    try:  # fallback: OS process list (no shell=True; explicit timeout)
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True, timeout=10).stdout
            return sum(1 for ln in out.splitlines()
                       if "python" in ln.lower() or "node" in ln.lower())
        out = subprocess.run(["ps", "-e", "-o", "comm"],
                             capture_output=True, text=True, timeout=10).stdout
        return sum(1 for ln in out.splitlines()
                   if ln.strip().lower().startswith(("python", "node")))
    except Exception:
        return None
