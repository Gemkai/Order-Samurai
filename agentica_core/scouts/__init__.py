"""System scouts — real local signals not derivable from telemetry. Honest by construction:
each returns a concrete count or None (never a fabricated value).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _count_autonomic_events(event_name: str) -> int | None:
    """Count records in the canonical autonomic_events.jsonl stream matching `event_name`.

    Reuses agentica_core.telemetry.default_events_path() (single source of truth for the
    path) rather than re-deriving it. Returns None if the module or file is unreachable —
    never fabricates a zero for a source that couldn't be read."""
    try:
        from agentica_core.telemetry import default_events_path
    except Exception:
        return None
    path = default_events_path()
    if not path.exists():
        return None
    count = 0
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("event") == event_name:
            count += 1
    return count


def _dependency_scanner_ok(dep: dict, scanner: str) -> bool:
    """Require an explicit healthy verdict; absent legacy health is unknown."""
    health = dep.get("scanner_ok")
    return isinstance(health, dict) and health.get(scanner) is True


def _dependency_scanner_skipped(dep: dict, scanner: str) -> bool:
    """A deliberately-skipped scanner contributes no scanner_ok entry at all
    (codebase_deps_audit.py: npm is opt-in via --npm) — absence in a modern
    health dict means "not run", never "ran and failed"."""
    health = dep.get("scanner_ok")
    return isinstance(health, dict) and scanner not in health


def _pip_cve_count(rows: object) -> int:
    if not isinstance(rows, list):
        return 0
    total = 0
    for row in rows:
        if isinstance(row, dict):
            count = row.get("vuln_count", 1)
            total += count if isinstance(count, int) and not isinstance(count, bool) else 1
        else:
            # Legacy fixtures/artifacts represented one finding as one scalar.
            total += 1
    return total


def _npm_cve_count(rows: object) -> int:
    if not isinstance(rows, list):
        return 0
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        count = row.get("total", 0)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            total += count
    return total


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
        expected = ("pip", "pip_audit", "npm")
        failed = sum(
            not _dependency_scanner_ok(dep, scanner)
            and not _dependency_scanner_skipped(dep, scanner)
            for scanner in expected
        )
        out["dependency_scanner_failures"] = failed

        # Completeness is required before publishing an exact zero or count.
        # A partial lower bound would still understate risk and look authoritative.
        # "Complete" means every scanner that RAN is healthy: a skipped npm scan
        # contributes an empty npm_audits, not unknown coverage of a dead scanner.
        if (_dependency_scanner_ok(dep, "pip_audit")
                and (_dependency_scanner_ok(dep, "npm")
                     or _dependency_scanner_skipped(dep, "npm"))):
            out["open_cves"] = (
                _pip_cve_count(dep.get("pip_cves"))
                + _npm_cve_count(dep.get("npm_audits"))
            )
        if _dependency_scanner_ok(dep, "pip"):
            outdated = dep.get("pip_outdated")
            if isinstance(outdated, list):
                out["deprecated_deps"] = len(outdated)

    # canary_failures RETIRED 2026-07-11 (C/D/F plan step 5): behavioral_canary.py
    # was never scheduled on this host, so canary_status.json is permanently absent —
    # a dark weight-3 metric. Re-add only together with a scheduled canary run.
    # (The security-gate self-test below is a DIFFERENT canary and stays.)

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
        # Scheduled_Job_Failures (2026-08-09): launchd jobs whose LAST RUN exited
        # non-zero, from mechanism_audit's check_launchd_exit_status. Counted off the
        # findings list rather than `counts` because that rollup is keyed by severity,
        # not category, and these land under the shared "warning" bucket. Absent key
        # (an older audit report predating the check) is left unset so the metric reads
        # as a data gap rather than a fabricated 0 — the house rule for a dark source.
        f = ma.get("findings")
        if isinstance(f, list):
            out["scheduled_job_failures"] = sum(
                1 for x in f
                if isinstance(x, dict) and x.get("category") == "launchd_failing"
            )

    dp = _read_json(data / "doc_parity.json")
    if isinstance(dp, dict):
        out["doc_parity_issues"] = (len(dp.get("broken_refs") or [])
                                    + len(dp.get("undocumented_hooks") or [])
                                    + len(dp.get("unwired_hooks") or []))

    # ARTS-001: live documentation-parity scout (Order Samurai repo)
    try:
        from agentica_core.scouts.doc_parity import run as _dp
        dp_live = _dp()
        # `is not None`: the live scout returns None when its git query failed, and this
        # line OVERWRITES the file-derived value computed just above — so a defaulted 0
        # would not merely publish a fake healthy number, it would erase a real one.
        if dp_live.get("doc_parity_issues") is not None:
            out["doc_parity_issues"] = dp_live["doc_parity_issues"]
    except Exception:
        pass

    # processes_reaped RETIRED 2026-07-08 audit: no reaper was ever ported to this
    # host (mcp_reaper.jsonl permanently absent) — the metric could only be fake.

    # security_scorecard RETIRED 2026-07-11: the Windows scripts-tier emitter is
    # gone and its content overlaps Guardrail_Blocks + Secrets_Detected +
    # Gate_Canary_Fault — removal, never faking.

    # skill_safety_findings RETIRED 2026-07-08 audit: no skill scanner exists on
    # this host and the mapped remediation audited dep packages, not skills —
    # re-introduce only together with a real scanner + quarantine bin.

    # skills_optimized + skill_promotions RETIRED 2026-07-19 (metric-surface review
    # Part E item 3): skill_improve_after_use_log.jsonl / skill_promotion_log.jsonl
    # are never written on this host — both counters were permanently dark.

    # Arts: craft signals — skill conflicts
    conf = _read_json(data / "skill_conflicts.json")
    if isinstance(conf, dict):
        out["skill_conflicts"] = len(conf.get("groups") or [])

    # AUTO-016: Knowledge Prompted — count of memory_recall autonomic events (<BRAND>³
    # recall telemetry: a Read of a memory/vault file during a session, emitted by
    # agentica_emit.py's SessionEnd hook into the canonical autonomic_events stream).
    kp = _count_autonomic_events("memory_recall")
    if kp is not None:
        out["knowledge_prompted"] = kp

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

    # AUTO-007: Vulnerability Window (Patch Latency) — age of the longest-open
    # unpatched CVE, from a PERSISTENT per-CVE first-seen ledger (scouts.
    # vulnerability_window). The old inline version used dependency_audit.json's
    # `generated_at`, which is stamped fresh on every scan — so a CVE open for
    # weeks always read back as ~0 days the moment the scanner re-ran. The ledger
    # makes first_seen persist across runs so the window genuinely grows.
    try:
        from agentica_core.scouts.vulnerability_window import update_and_measure as _vuln_window
        _vw = _vuln_window(runtime_root)
        if _vw.get("vulnerability_window_days") is not None:
            out["vulnerability_window_days"] = _vw["vulnerability_window_days"]
    except Exception:
        pass

    # SWORD-kill_chain_discovery: discover untracked kill chains from telemetry
    try:
        from agentica_core.scouts.kill_chain_discovery import run as _kcd
        kcd = _kcd(runtime_root)
        # `is not None`, not `.get(..., 0)`: the scout reports None when it could not read
        # the taxonomy, and 0 is this signal's healthiest value — recording the default
        # would publish "no untracked kill chains" on the strength of an absent source.
        # Same shape as the vulnerability_window check two blocks above.
        if kcd.get("kill_chain_candidates") is not None:
            out["kill_chain_candidates"] = kcd["kill_chain_candidates"]
    except Exception:
        pass

    return out


# score_security_posture removed 2026-07-11 with the Security_Scorecard retirement
# (dead Windows scripts-tier emitter; content overlapped Guardrail_Blocks +
# Secrets_Detected + Gate_Canary_Fault).


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


# --- Knowledge layer (Memory) --------------------------------------------------------------

_INDEX_LINK = re.compile(r"\]\(([^)#\s]+\.md)\)")


def _load_okf_tools(repo_root: Path):
    """Load Knowledge/okf/okf_tools.py by explicit path. No sys.path/sys.modules pollution,
    so tests can point at throwaway roots without hitting Python's import cache."""
    mod_path = repo_root / "Knowledge" / "okf" / "okf_tools.py"
    if not mod_path.is_file():
        return None
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location("_agentica_okf_tools", mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _index_drift(d: Path) -> int | None:
    """Symmetric difference between what index.md lists and the concept files on disk.
    None when there is no index.md to check (that absence is its own verifier finding)."""
    idx = d / "index.md"
    if not idx.is_file():
        return None
    try:
        listed = {Path(m).name for m in _INDEX_LINK.findall(idx.read_text(encoding="utf-8"))}
        actual = {p.name for p in d.glob("*.md")} - {"index.md", "log.md"}
        return len(listed ^ actual)
    except OSError:
        return None


def knowledge_signals(repo_root: Path | None = None) -> dict:
    """Knowledge-layer health signals (platform-independent — compute once per aggregate).

    Reads what the Knowledge layer already publishes: the OKF toolkit's view of
    Knowledge/vault, vault/me file ages, and the dashboard's graph.json. Missing
    sources are omitted from the dict — never fabricated (tier-honesty)."""
    root = repo_root or Path(__file__).resolve().parents[3]
    out: dict = {}
    vault = root / "Knowledge" / "vault"

    okf = _load_okf_tools(root)
    if okf is not None and vault.is_dir():
        try:
            total = ok = 0
            for p in okf.iter_concepts(vault):
                total += 1
                if okf.read_concept(vault, p)["conformant"]:
                    ok += 1
            if total:
                out["okf_total_concepts"] = total
                out["okf_conformance_pct"] = round(100.0 * ok / total, 1)
        except OSError:
            pass

    me = vault / "me"
    try:
        mtimes = [f.stat().st_mtime for f in me.glob("*.md")]
    except OSError:
        mtimes = []
    if mtimes:
        out["knowledge_staleness_days"] = round(
            (datetime.now(timezone.utc).timestamp() - max(mtimes)) / 86400.0, 1)

    drift = _index_drift(me)
    if drift is not None:
        out["index_drift"] = drift

    # SOJI: vault link-integrity scanner (Execution/soji_scan.py, scheduled via
    # com.agentica.soji-cycle). Reads its findings.json the same way doc_parity.json
    # is read above — missing/malformed file is simply omitted, never a fabricated 0.
    # Staleness guard: a `generated_at` older than 7 days — or absent/unparseable —
    # degrades to the same omitted state as a missing file (silence != health).
    # 7 days matches scorecard.py's know_soji_findings_fresh probe (config/
    # scorecard_rubric.json: params.max_age_days=7), the codebase's existing
    # freshness budget for this exact artifact; the inline-parse shape follows the
    # security_gate_canary staleness check above (a stale all-clear is not an
    # all-clear).
    soji = _read_json(root / "Data" / "soji" / "memory.findings.json")
    if isinstance(soji, dict) and isinstance(soji.get("findings"), list):
        soji_stale = True
        gen_at = soji.get("generated_at")
        if gen_at:
            try:
                t = datetime.fromisoformat(str(gen_at).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                soji_stale = (datetime.now(timezone.utc) - t).days > 7
            except ValueError:
                soji_stale = True
        if not soji_stale:
            kinds = [f.get("kind") for f in soji["findings"] if isinstance(f, dict)]
            out["soji_broken_links"] = kinds.count("broken_link")
            out["soji_orphan_notes"] = kinds.count("orphan_note")

    gpath = root / "Knowledge" / "dashboard" / "graph.json"
    g = _read_json(gpath)
    if isinstance(g, dict) and isinstance(g.get("nodes"), list):
        nodes = g["nodes"]
        linked: set = set()
        for pair in g.get("links", []):
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                linked.update(pair)
        mem_idx = [i for i, n in enumerate(nodes) if isinstance(n, dict) and n.get("ring") == 2]
        if mem_idx:
            archive = sum(1 for i in mem_idx if nodes[i].get("cluster") == "ARCHIVE")
            out["archive_ratio_pct"] = round(100.0 * archive / len(mem_idx), 1)
            out["orphan_concepts"] = sum(
                1 for i in mem_idx
                if i not in linked and nodes[i].get("cluster") != "ARCHIVE")
        try:
            out["graph_age_days"] = round(
                (datetime.now(timezone.utc).timestamp() - gpath.stat().st_mtime) / 86400.0, 2)
        except OSError:
            pass
    return out
