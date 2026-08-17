from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, NamedTuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import governance_root
from execution.verify_archive_boundaries import run_checks as run_archive_boundary_checks
from execution.verify_path_authority import run_checks as run_path_authority_checks
from execution.verify_root_hygiene import run_checks as run_root_hygiene_checks
from execution.verify_agentica_root_hygiene import run_checks as run_agentica_root_hygiene_checks
from execution.verify_no_stale_paths import run_checks as run_stale_path_checks
from execution.verify_live_sources import run_checks as run_live_source_checks
from execution.verify_runtime_contract import run_checks as run_runtime_contract_checks
from execution.verify_telemetry_freshness import run_checks as run_telemetry_freshness_checks
from execution.score_claude_architecture import run_checks as run_claude_arch_checks


def _run_meditation_timestamp_checks() -> list[dict]:
    """WARN on done/doing backlog items missing their calibration timestamps.

    Calibration coefficients only accumulate from (started_at, completed_at)
    pairs — an unstamped done item is a silently lost sample.
    Fix: python bin/stamp_meditation_timestamps.py
    """
    import json
    import subprocess
    state = ROOT_DIR / "state" / "MEDITATION_STATE.json"
    if not state.exists():
        return []
    # Pre-doctor backstop: the stamp script is the CODE chokepoint for the
    # at-dispatch/at-transition capture the meditation prompt (Step C/F) is
    # merely instructed to do. Running it here means every doctor invocation
    # (Step A-prime, Step E, sensei-cycle, manual) closes the leak window, so
    # the WARNs below only report what code could not recover. Idempotent; a
    # backstop failure must never break the health check itself.
    stamp = ROOT_DIR / "bin" / "stamp_meditation_timestamps.py"
    if stamp.exists():
        try:
            subprocess.run([sys.executable, str(stamp)], capture_output=True,
                           timeout=30, check=False)
        except Exception:
            pass
    try:
        backlog = json.loads(state.read_text(encoding="utf-8")).get("backlog", [])
    except Exception as exc:
        return [{"status": "WARN", "label": "meditation-timestamps",
                 "detail": f"MEDITATION_STATE.json unreadable: {exc}"}]
    # Recoverable: a backstop run of stamp_meditation_timestamps.py can fill these.
    recoverable = [i.get("id", "?") for i in backlog
                   if (i.get("status") == "done" and not i.get("completed_at"))
                   or (i.get("status") == "doing" and not i.get("started_at"))]
    # Lost: a done item with no started_at has no honest source for it (commit-span
    # is not work-duration; stamping it = a fabricated 0-min sample). The real fix is
    # forward — stamp started_at at dispatch, not only at cycle end. IDs in the
    # explicit baseline file are acknowledged-lost (pre-chokepoint history): excluded
    # here so this WARN only fires on NEW leaks, i.e. the transition-backstop in
    # stamp_meditation_timestamps.py failed to observe a transition.
    baseline_file = ROOT_DIR / "state" / "calibration_lost_baseline.json"
    baselined_ids: set = set()
    try:
        baselined_ids = set(json.loads(baseline_file.read_text(encoding="utf-8"))
                            .get("baselined", []))
    except Exception:
        pass
    all_lost = [i.get("id", "?") for i in backlog
                if i.get("status") == "done" and not i.get("started_at")]
    lost = [i for i in all_lost if i not in baselined_ids]
    baselined_seen = len(all_lost) - len(lost)
    results: list[dict] = []
    if recoverable:
        results.append({"status": "WARN", "label": "meditation-timestamps",
                        "detail": f"{len(recoverable)} item(s) missing recoverable timestamps "
                                  f"({', '.join(recoverable[:5])}) — run bin/stamp_meditation_timestamps.py"})
    if lost:
        results.append({"status": "WARN", "label": "meditation-timestamps.lost-samples",
                        "detail": f"{len(lost)} done item(s) missing started_at with no recoverable source "
                                  f"({', '.join(lost[:5])}) — calibration samples permanently lost; fix "
                                  f"forward capture (stamp started_at at dispatch, not only at cycle end)"})
    if baselined_seen:
        results.append({"status": "OK", "label": "meditation-timestamps.lost-samples",
                        "detail": f"{baselined_seen} acknowledged-lost item(s) excluded via "
                                  f"state/calibration_lost_baseline.json"})
    if not results:
        # An empty done/doing set satisfies "all of them carry timestamps" trivially,
        # so the old unconditional OK read GREEN while the sample rate was zero —
        # indistinguishable from a healthy cycle. Observed 2026-07-29: 9 backlog
        # items, all todo, cycle 0, calibration frozen, and this row said OK.
        # Report the population the claim is made over, so "nothing to check" can
        # never again be mistaken for "checked and healthy".
        timed = sum(1 for i in backlog
                    if i.get("status") in ("done", "doing")
                    and (i.get("started_at") or i.get("completed_at")))
        if timed:
            results.append({"status": "OK", "label": "meditation-timestamps",
                            "detail": f"all {timed} done/doing backlog item(s) carry "
                                      f"calibration timestamps"})
        else:
            # Deliberately OK, not WARN. The condition is real and worth reading, but
            # doctor's WARN count feeds the meditation cycle's own A-prime gate
            # (halt when current > baseline) — so raising it here would halt the very
            # cycle that produces the missing samples. That circular shape is the bug
            # this row was rewritten to expose, not one to add. The honesty belongs in
            # the DETAIL: it states the zero instead of implying health.
            results.append({"status": "OK", "label": "meditation-timestamps.no-samples",
                            "detail": f"nothing to stamp: 0 done/doing items among "
                                      f"{len(backlog)} backlog item(s), so 0 calibration "
                                      f"samples are accruing and the Agent-Time-Saved "
                                      f"coefficients cannot advance toward their threshold"})
    return results


def _run_schema_violation_checks(state_dir: Path | None = None, now=None) -> list[dict]:
    """Report how many consecutive clean days the Phase A2 warn-only schema sink has.

    A3 flips `sensei_writeback` from warn-only to enforce after 7 clean days. That
    gate needs something that actually counts the days — otherwise "7 clean days"
    is a claim nobody measured. This is the observer, and it lives here rather than
    in its own scheduled job because doctor already runs on the meditation cadence
    and already reads state/ (Mechanism budget: no new launchd entry).

    WARN-only on purpose. A schema violation is the signal A3 is waiting FOR; a
    gate that FAILs doctor on one would halt the overnight cycle over exactly the
    observation it exists to collect.

    The counter resets from the newest violation, not from the stamp: a stamp that
    outlived a violation would report a clean streak that never happened.
    """
    import json
    from datetime import datetime, timezone

    state_dir = state_dir or (ROOT_DIR / "state")
    stamp_path = state_dir / "schema_violations_clean_since.json"
    sink_path = state_dir / "schema_violations.jsonl"
    label = "schema-violations-clean"

    if not stamp_path.exists():
        return [{"status": "WARN", "label": label,
                 "detail": f"no clean-since stamp at {stamp_path.name} — A3's 7-day gate "
                           f"has no start date to count from"}]
    try:
        since = datetime.fromisoformat(
            json.loads(stamp_path.read_text(encoding="utf-8"))["clean_since"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{"status": "WARN", "label": label,
                 "detail": f"{stamp_path.name} unreadable or missing clean_since: {exc}"}]
    if since.tzinfo is None:
        # The stamp is hand-maintained; a naive value (datetime.now().isoformat(),
        # a bare date) must grade as UTC — the comparisons below are against
        # tz-aware datetimes and a TypeError would abort the whole doctor run.
        # Same grading as _run_claude_telemetry_checks' stamp handling.
        since = since.replace(tzinfo=timezone.utc)

    # Newest violation timestamp in the sink, if the sink exists at all. Absent
    # sink == zero violations: check_warn_only creates it lazily on the first one.
    newest = None
    violations = 0
    if sink_path.exists():
        try:
            for line in sink_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                except (ValueError, TypeError, KeyError):
                    continue
                violations += 1
                if newest is None or ts > newest:
                    newest = ts
        except OSError as exc:
            return [{"status": "WARN", "label": label,
                     "detail": f"{sink_path.name} unreadable: {exc}"}]

    # A violation newer than the stamp restarts the count from that violation.
    reset_by = newest if newest and newest > since else None
    now = now or datetime.now(timezone.utc)
    days = (now - (reset_by or since)).total_seconds() / 86400.0

    if reset_by:
        return [{"status": "WARN", "label": label,
                 "detail": f"{violations} violation(s) recorded; newest at "
                           f"{reset_by.isoformat()} reset the streak to {days:.1f}d — "
                           f"A3 cannot flip until it reaches 7d"}]
    return [{"status": "OK", "label": label,
             "detail": f"{days:.1f} clean day(s) since {since.date()} "
                       f"({'A3 flip-eligible' if days >= 7 else 'A3 gate: 7d'})"}]


def _run_local_llm_checks() -> list[dict]:
    """WARN when the local LLM (Ollama) endpoint is unreachable.

    The model router (agentica_core.model_router) and bin/ronin-local route
    classification/bulk work to a local Ollama server, falling back to paid
    cloud APIs on failure. That fallback is SILENT: a dead local tier surfaces
    only as higher cost and a collapsing Local_Routing_Share (which reads None,
    not 0, when there are no local records) -- never as an error. This probe
    converts that silent outage into a visible WARN the daemon-health gate
    catches. Fix: start Ollama ('ollama serve') or the Ollama desktop app.
    """
    import json
    import os
    import urllib.request

    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as resp:
            models = [m.get("name") for m in json.loads(resp.read()).get("models", [])]
    except Exception as exc:
        return [{"status": "WARN", "label": "local-llm",
                 "detail": f"Ollama unreachable at {base} ({exc.__class__.__name__}); local "
                           f"routing is silently falling back to paid cloud APIs -- start "
                           f"Ollama ('ollama serve') or the desktop app"}]
    if not models:
        return [{"status": "WARN", "label": "local-llm",
                 "detail": f"Ollama reachable at {base} but no models pulled -- local routing "
                           f"will fall back to cloud (run 'ollama pull gemma4:4b')"}]
    return [{"status": "OK", "label": "local-llm",
             "detail": f"Ollama reachable at {base} ({len(models)} model(s): "
                       f"{', '.join(m for m in models[:3] if m)})"}]


def _run_container_service_checks() -> list[dict]:
    """WARN when a locally-dependent service (Qdrant, Ollama) is unreachable.

    Complements _run_local_llm_checks (Ollama only) with the other local dependency this
    fleet leans on. Reuses Governance/bin/fleet_probe.py's reachability check rather than
    re-deriving it (Anti-Pattern #2) -- that module is also the source of fleet_probe.json,
    which hitl_alerts.py's 30-min FLEET HEALTH banner reads, so a doctor run and a notifier
    run agree on what "unreachable" means. Single-shot (attempts=1): doctor runs
    interactively and from the dojo STEP A-prime gate, where fleet_probe's ~40s DarkWake
    backoff window would make every doctor invocation noticeably slower for a check that
    already re-runs every 30 minutes via the notifier.

    Known blind spot (documented, not fixed here): this only answers "is the port
    answering right now" -- deliberately the narrowest, hardest-to-fake signal available.
    It does NOT read container status, because `docker ps` can report "Up" while the port
    inside is still dead moments after a DarkWake-suspended VM resumes (see
    darkwake-suspends-orbstack-scheduled-jobs memory) -- a container-status check would
    report false-green through the exact incident this exists to catch.
    """
    governance_bin = ROOT_DIR.parent / "bin"
    if str(governance_bin) not in sys.path:
        sys.path.insert(0, str(governance_bin))
    try:
        from fleet_probe import check_unreachable_services  # type: ignore
    except Exception as exc:
        return [{"status": "WARN", "label": "container-services",
                 "detail": f"fleet_probe import failed ({exc}) -- service reachability unverified"}]
    try:
        unreachable = check_unreachable_services(attempts=1)
    except Exception as exc:
        return [{"status": "WARN", "label": "container-services",
                 "detail": f"reachability probe raised ({exc}) -- service reachability unverified"}]
    if unreachable:
        return [{"status": "WARN", "label": "container-services",
                 "detail": f"unreachable: {', '.join(unreachable)} -- check OrbStack/Docker "
                           f"and whether the host recently resumed from DarkWake"}]
    return [{"status": "OK", "label": "container-services",
             "detail": "all locally-dependent services reachable"}]


def _resolve_engine_python_bin(exe_name: str, plist_path: Path | None = None) -> tuple[str, str | None]:
    """Resolve `exe_name` the way the reflex engine's launchd PATH would —
    NOT doctor's own process PATH.

    The engine is the node process launched by
    ~/Library/LaunchAgents/com.agentica.order-samurai-api.plist, whose
    EnvironmentVariables > PATH key is explicit and puts mise python first
    (see that key's own comment re: SENSEI-7). doctor.py itself is invoked
    from ronin-daemon.sh, the dojo STEP A-prime gate, and interactive shells
    — none of which inherit the plist's PATH. Resolving a bare exe_name from
    doctor's OWN PATH can stay green while the plist's PATH is broken (e.g.
    reverted to /usr/bin:/bin), because the two PATHs are independent and
    commonly differ. This reads the plist's actual PATH and resolves
    `exe_name` against IT, so the canary tests the interpreter the engine
    will really get.

    Returns (resolved_path, warning_or_None). When the plist is missing,
    unreadable, or has no PATH key, falls back to sys.executable — the
    interpreter doctor is demonstrably running — with a warning string, so the
    canary still runs rather than hard-failing. It deliberately does NOT fall
    back to the bare exe name: that resolves against whatever PATH doctor's
    caller happened to export, which is a third unknown interpreter that is
    neither the engine's nor doctor's. Blaming the gate for an interpreter the
    canary itself invented is the failure this fallback exists to avoid. The
    warning is what carries the real news — that engine parity is unproved.
    """
    import plistlib
    import shutil

    plist_path = plist_path or (Path.home() / "Library" / "LaunchAgents" /
                                 "com.agentica.order-samurai-api.plist")
    if not plist_path.exists():
        return sys.executable, (f"plist not found at {plist_path} — falling back to "
                                 f"doctor's own interpreter, which may not match the engine's")
    try:
        with open(plist_path, "rb") as fh:
            plist = plistlib.load(fh)
        engine_path = plist["EnvironmentVariables"]["PATH"]
    except (OSError, KeyError, plistlib.InvalidFileException) as exc:
        return sys.executable, (f"could not read PATH from {plist_path}: {exc} — falling "
                                 f"back to doctor's own interpreter, which may not match "
                                 f"the engine's")

    resolved = shutil.which(exe_name, path=engine_path)
    if resolved is None:
        return sys.executable, (f"'{exe_name}' not found on the engine's plist PATH "
                                 f"({engine_path}) — falling back to doctor's own interpreter")
    return resolved, None


def _run_audit_gate_canary_checks(python_bin: str | None = None,
                                  script: Path | None = None,
                                  plist_path: Path | None = None) -> list[dict]:
    """WARN when the maker-checker audit gate cannot even start under the
    interpreter the reflex engine spawns.

    The engine invokes execution/audit_remediation_patch.py with a BARE
    'python3' ('python' on Windows) resolved from ITS OWN process PATH — the
    plist's EnvironmentVariables > PATH key, not sys.executable and not
    doctor's own PATH (reflex-engine.ts, maker-checker audit spawn). A PATH
    that resolves to the CommandLineTools system python (no 'requests',
    transitive via agentica_core.llm.gateway) kills the audit at import time
    with exit 2 ('audit_rejected', fail closed) — the exact outage that
    silently rejected the only two real patches ever produced (2026-07-20/26).
    The Aug-1 plist PATH change is only 'plausibly fixed' until something
    re-proves it against THAT PATH specifically; this canary does, on every
    doctor run: spawn the gate the way the engine does (interpreter resolved
    from the plist's own PATH via _resolve_engine_python_bin, cwd=Order
    Samurai, GOVERNANCE_ROOT in env) against a benign synthetic patch. The
    patch is EMPTY on purpose — empty is the one approve-by-default path that
    exercises the full import chain without reaching the LLM leg, so the
    canary stays fast/deterministic and never spends a gemma4:12b call
    (Ollama liveness is _run_local_llm_checks' job). `python_bin`/`script` are
    injectable for tests — passing python_bin explicitly (as most tests do)
    skips plist resolution entirely; `plist_path` is separately injectable so
    plist-resolution itself can be tested without touching the real one on
    disk. Kill switches: AUDIT_CANARY_ENABLED=false disables the whole canary
    (default on); AUDIT_CANARY_RESOLVE_VIA_PLIST=false skips plist resolution
    and runs under sys.executable for rollback (default on — resolve via the
    plist). Switched off, this checks that DOCTOR's interpreter can start the
    gate — it says nothing about engine parity, which is the whole point of the
    plist path.
    """
    import os
    import subprocess
    import tempfile

    label = "audit-gate-canary"
    if os.environ.get("AUDIT_CANARY_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        return []

    script = script or (ROOT_DIR / "execution" / "audit_remediation_patch.py")
    if not script.exists():
        return [{"status": "WARN", "label": label,
                 "detail": f"audit script missing at {script} — the maker-checker "
                           f"gate cannot run at all"}]

    resolve_warning = None
    if python_bin is None:
        exe_name = "python" if sys.platform == "win32" else "python3"
        if os.environ.get("AUDIT_CANARY_RESOLVE_VIA_PLIST", "true").strip().lower() in ("false", "0", "no"):
            python_bin = sys.executable
        else:
            python_bin, resolve_warning = _resolve_engine_python_bin(exe_name, plist_path=plist_path)
    # resolve_warning does NOT short-circuit: the canary still runs against
    # whatever interpreter it fell back to (matches _resolve_engine_python_bin's
    # own "still runs rather than hard-failing" contract) -- but a run that
    # didn't verifiably use the plist's interpreter must not report bare OK,
    # since "OK" is exactly the claim that was false during the outage.

    def _warn(detail: str) -> list[dict]:
        """WARN carrying every fact learned, not just the last one. A spawn
        failure under a fallback interpreter is TWO findings -- the gate did not
        start, and parity with the engine was never established. Reporting only
        the spawn failure reads as "the gate is broken" and sends the reader to
        debug a gate that may be fine."""
        if resolve_warning:
            detail = (f"{detail}; also could not confirm this is the engine's actual "
                      f"interpreter: {resolve_warning}")
        return [{"status": "WARN", "label": label, "detail": detail}]

    patch_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", prefix="audit_canary_",
                                         delete=False) as fh:
            patch_path = fh.name  # left empty: approve-by-default, no LLM call
        proc = subprocess.run(
            [python_bin, str(script), "--patch", patch_path],
            cwd=str(ROOT_DIR),
            env={**os.environ, "GOVERNANCE_ROOT": str(governance_root())},
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return _warn(f"interpreter '{python_bin}' not on PATH — the reflex "
                     f"engine's audit spawn fails the same way, so every "
                     f"remediation patch would be audit_rejected")
    except subprocess.TimeoutExpired:
        return _warn(f"audit canary timed out after 60s under '{python_bin}' — "
                     f"gate liveness unverified")
    finally:
        if patch_path:
            try:
                os.unlink(patch_path)
            except OSError:
                pass

    if proc.returncode != 0:
        # exit 2 is the import-time fail-closed path; surface the script's own
        # first line, which names the missing module + interpreter.
        lines = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()
        first = lines[0].strip()[:300] if lines and lines[0].strip() else "no output"
        return _warn(f"audit gate exited {proc.returncode} under '{python_bin}' "
                     f"on a benign patch — every remediation patch would be "
                     f"audit_rejected: {first}")

    detail = (f"audit gate imports and approves a benign patch under "
              f"'{python_bin}'")
    if resolve_warning:
        return [{"status": "WARN", "label": label,
                 "detail": f"{detail}, but could not confirm this is the engine's "
                           f"actual interpreter (fell back to doctor's own "
                           f"interpreter): {resolve_warning}"}]
    return [{"status": "OK", "label": label,
             "detail": f"{detail} (resolved via the engine's own plist PATH)"}]


def _run_exec_chain_checks() -> list[dict]:
    """FAIL when exec_log.jsonl's tamper-evident hash chain does not recompute.

    exec_log.jsonl is the "verified means ran" record: the reflex engine writes its
    own improved/metric_after verdicts there and rival post-audits them later. Each
    row is chained (seq + prev_hash + entry_hash) so editing a past verdict breaks
    every hash after it. That only detects anything if something WALKS the chain --
    a chained-but-never-verified ledger is a producer orphan. This is that walk.

    The chain logic has ONE implementation (Governance/api/src/hash-chain.ts); this
    shells out to it rather than re-deriving the hash in Python, which would drift
    (Anti-Pattern #2). A missing node/tsx toolchain WARNs -- it means the check could
    not run, which is not the same claim as "the ledger is intact".
    """
    import json
    import subprocess

    api_dir = Path(__file__).resolve().parents[2] / "api"
    if not (api_dir / "src" / "verify-chain-cli.ts").exists():
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": f"verifier missing at {api_dir}/src/verify-chain-cli.ts -- "
                           f"exec_log tamper-evidence is unverified"}]
    try:
        proc = subprocess.run(
            ["npx", "tsx", "src/verify-chain-cli.ts"],
            cwd=str(api_dir), capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": "npx not on PATH -- exec_log tamper-evidence is unverified "
                           "(install Node, then re-run doctor)"}]
    except subprocess.TimeoutExpired:
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": "chain verification timed out after 60s -- exec_log "
                           "tamper-evidence is unverified"}]

    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": f"chain verifier returned unparseable output "
                           f"(exit {proc.returncode}): {proc.stderr.strip()[:200]}"}]

    chained, unchained = result.get("chained", 0), result.get("unchained", 0)
    if not result.get("ok"):
        return [{"status": "FAIL", "label": "exec-chain",
                 "detail": f"exec_log hash chain BROKEN at seq {result.get('brokenAtSeq')}: "
                           f"{result.get('reason')} -- a past remediation verdict was edited "
                           f"after it was written; inspect state/exec_log.jsonl from that row"}]
    if chained == 0:
        return [{"status": "OK", "label": "exec-chain",
                 "detail": f"no chained rows yet ({unchained} pre-migration row(s)); "
                           f"chaining starts at the next reflex run"}]
    return [{"status": "OK", "label": "exec-chain",
             "detail": f"exec_log hash chain intact ({chained} chained row(s) verified, "
                       f"{unchained} pre-migration)"}]


def _run_claude_telemetry_checks(max_age_hours: float = 48.0,
                                 source: "Path | None" = None,
                                 now=None) -> list[dict]:
    """FAIL (not WARN) when the newest Claude telemetry record exceeds max_age_hours.

    The SessionEnd emitter swallows every error by design ("never break a
    session over telemetry"), so a dead emitter leaves no trace anywhere — the
    2026-06-21 → 2026-07-06 outage ran 15 days undetected while dashboards
    quietly served stale metrics. Record recency is the only liveness signal
    the pipeline has, so staleness must gate (doctor exit 1), not annotate.

    `source`/`now` are injectable for tests; at runtime the sink path comes
    from the kernel's platform registry — never hardcode it here.
    """
    from datetime import datetime, timezone

    governance = ROOT_DIR.parent
    if str(governance) not in sys.path:
        sys.path.insert(0, str(governance))
    try:
        from agentica_core.adapter import resolve_platform
        from agentica_core.telemetry import parse_ts
    except Exception as exc:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"kernel import failed ({exc}) — cannot resolve telemetry source"}]
    if source is None:
        try:
            source = resolve_platform("claude").telemetry_source
        except Exception as exc:
            return [{"status": "FAIL", "label": "claude-telemetry",
                     "detail": f"platform registry unresolvable ({exc})"}]
    if not source.exists():
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"no telemetry file at {source} — the SessionEnd emitter "
                           f"has never landed a record on this machine"}]

    import json
    newest = None
    try:
        # errors="ignore" matches verify_telemetry_freshness: a truncated multibyte
        # tail from a killed write must not false-FAIL the gate as "unreadable".
        with source.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    dt = parse_ts(json.loads(line).get("timestamp"))
                except Exception:
                    continue
                if dt is None:
                    continue
                # Grade naive stamps as UTC before comparing: a file mixing naive
                # and tz-aware timestamps must not TypeError into the outer except
                # and false-FAIL the gate as "unreadable".
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if newest is None or dt > newest:
                    newest = dt
    except Exception as exc:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"telemetry file unreadable: {exc}"}]
    if newest is None:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"{source} contains no parseable timestamped records"}]

    now = now or datetime.now(timezone.utc)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    age_h = (now - newest).total_seconds() / 3600.0
    if age_h > max_age_hours:
        return [{"status": "FAIL", "label": "claude-telemetry",
                 "detail": f"newest record is {age_h:.1f}h old (limit {max_age_hours:.0f}h) — "
                           f"the emitter is silently dead; check the SessionEnd hook and "
                           f"~/.claude/scripts/agentica_emit.py"}]
    return [{"status": "OK", "label": "claude-telemetry",
             "detail": f"newest record {age_h:.1f}h old (limit {max_age_hours:.0f}h)"}]


def _run_factory_checks(repo_root: Path | None = None,
                        launch_agents_dir: Path | None = None) -> list[dict]:
    """WARN-only probes for the hands-off factory substrate (plan M0.4, 2026-08-09).

    Four checks, scoped to what exists pre-dispatcher:
      * queue files reachable and schema-shaped (the dispatcher's poll surface) — a corrupt
        queue must be visible BEFORE a dispatcher exists to mis-read it;
      * ``gh auth status`` — merge-submit workers and PR flows die silently without it;
      * launchd plist source-vs-installed drift — the documented "edited the repo plist,
        launchd still runs the stale installed copy" failure mode;
      * factory ledger (``Execution/factory/state/ledger.jsonl``) hash-chain integrity
        (plan M3.2, architecture doc D3) — added by the ledger milestone itself, not the
        dispatcher, so verify_chain has a consumer from the day the file can first exist.

    WARN-only like local-llm: the factory is not live yet, so these must be VISIBLE but
    must not gate doctor's exit code. Dispatcher/merge-lane liveness probes are added by
    their own milestones (plan M3.1/M1.2), not here.
    """
    root = repo_root or Path(__file__).resolve().parents[3]
    installed_dir = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    return [
        *_factory_queue_checks(root),
        *_factory_gh_auth_checks(),
        *_factory_plist_drift_checks(root, installed_dir),
        *_factory_ledger_chain_checks(root),
    ]


def _factory_queue_checks(root: Path) -> list[dict]:
    """The dispatcher's poll surface: a corrupt queue must be visible BEFORE a
    dispatcher exists to mis-read it."""
    import json

    results: list[dict] = []
    # -- queue reachability + schema ------------------------------------------------
    registry = root / ".planning" / "GOAL_REGISTRY.jsonl"
    backlog = root / "Governance" / "Order Samurai" / "state" / "PROPOSED_BACKLOG.json"
    try:
        bad = 0
        for line in registry.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                json.loads(line)
            except ValueError:
                bad += 1
        if bad:
            results.append({"status": "WARN", "label": "factory.queue",
                            "detail": f"{registry.name}: {bad} unparseable row(s)"})
        else:
            results.append({"status": "OK", "label": "factory.queue",
                            "detail": f"{registry.name} parseable"})
    except OSError as exc:
        results.append({"status": "WARN", "label": "factory.queue",
                        "detail": f"{registry} unreadable: {exc}"})
    try:
        data = json.loads(backlog.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            results.append({"status": "OK", "label": "factory.queue",
                            "detail": f"{backlog.name} schema-shaped ({len(data['items'])} items)"})
        else:
            results.append({"status": "WARN", "label": "factory.queue",
                            "detail": f"{backlog.name}: missing top-level items list"})
    except (OSError, ValueError) as exc:
        results.append({"status": "WARN", "label": "factory.queue",
                        "detail": f"{backlog.name} unreadable/unparseable: {exc}"})

    return results


def _factory_gh_auth_checks() -> list[dict]:
    """Merge-submit workers and PR flows die silently without gh auth."""
    import subprocess

    results: list[dict] = []
    # -- gh auth --------------------------------------------------------------------
    try:
        proc = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                              timeout=15)
        if proc.returncode == 0:
            results.append({"status": "OK", "label": "factory.gh-auth",
                            "detail": "gh auth status: authenticated"})
        else:
            results.append({"status": "WARN", "label": "factory.gh-auth",
                            "detail": f"gh auth status exited {proc.returncode}"})
    except FileNotFoundError:
        results.append({"status": "WARN", "label": "factory.gh-auth",
                        "detail": "gh CLI not found — cannot verify"})
    except subprocess.TimeoutExpired:
        results.append({"status": "WARN", "label": "factory.gh-auth",
                        "detail": "gh auth status timed out"})

    return results


def _factory_plist_drift_checks(root: Path, installed_dir: Path) -> list[dict]:
    """The documented failure: the repo plist was edited, launchd still runs the
    stale installed copy."""
    results: list[dict] = []
    # -- plist source-vs-installed drift -------------------------------------------
    sources = sorted(
        list((root / "Governance" / "automation" / "launchd").glob("com.agentica.*.plist"))
        + list((root / "Governance" / "bin").glob("com.agentica.*.plist"))
    )
    drifted = []
    for src in sources:
        twin = installed_dir / src.name
        if not twin.exists():
            continue  # pre-staged, never installed — normal here
        try:
            if src.read_bytes() != twin.read_bytes():
                drifted.append(src.name)
        except OSError:
            drifted.append(f"{src.name} (unreadable)")
    if drifted:
        results.append({"status": "WARN", "label": "factory.plist-drift",
                        "detail": "source != installed: " + ", ".join(drifted)})
    else:
        results.append({"status": "OK", "label": "factory.plist-drift",
                        "detail": f"{len(sources)} source plist(s), no drift vs installed"})

    return results


def _factory_ledger_chain_checks(root: Path) -> list[dict]:
    """Hash-chain integrity for the factory ledger (plan M3.2, architecture D3)."""
    import importlib.util

    results: list[dict] = []
    # -- factory ledger hash-chain integrity (plan M3.2, D3) ------------------------
    ledger_module_path = root / "Execution" / "factory" / "ledger.py"
    ledger_log_path = root / "Execution" / "factory" / "state" / "ledger.jsonl"
    spec = importlib.util.spec_from_file_location("_factory_ledger_probe", ledger_module_path)
    if spec is None or spec.loader is None:
        results.append({"status": "WARN", "label": "factory.ledger-chain",
                        "detail": f"{ledger_module_path} not found — cannot verify"})
    else:
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            verdict = module.verify_chain(ledger_log_path)
        except (OSError, ValueError, SyntaxError) as exc:
            results.append({"status": "WARN", "label": "factory.ledger-chain",
                            "detail": f"could not load/run ledger.verify_chain: {exc}"})
        else:
            if verdict["ok"]:
                results.append({"status": "OK", "label": "factory.ledger-chain",
                                "detail": f"{verdict['chained']} chained row(s), "
                                          f"{verdict['unchained']} unchained"})
            else:
                results.append({"status": "WARN", "label": "factory.ledger-chain",
                                "detail": f"broken at seq {verdict['broken_at_seq']}: "
                                          f"{verdict['reason']}"})
    return results


class _Family(NamedTuple):
    """One doctor check family: how to run it, render it, and count it.

    Replaces ~130 lines that ran, printed, summarized and hand-summed 17 families
    in four parallel blocks — where a new family meant four edits in four places
    and an omission was invisible. The declaration below is the single place any
    of that is stated.

    gating   FAIL rows feed BOTH total_fail and the exit code. The two were
             always the same set, so they are one flag rather than two that can
             disagree. A non-gating family's FAIL is deliberately not counted:
             these probes report on a live workstation, and doctor does not fail
             on them (each family's own comment says why it is a spectator).
    warns    False only for claude-telemetry, whose WARN rows have never been
             counted in the summary. Preserved verbatim rather than "fixed":
             this refactor is behaviour-preserving, and changing a published
             count is a separate, deliberate decision.
    label_key  root-hygiene's agentica variant emits `name` where every other
             family emits `label`.
    """
    name: str
    runner: Callable[[], list[dict]]
    gating: bool
    label_key: str = "label"
    warns: bool = True


#: Run and print order. This IS the doctor's output order — the list is the spec.
CHECK_FAMILIES: tuple[_Family, ...] = (
    _Family("path-authority", run_path_authority_checks, gating=True),
    _Family("stale-paths", run_stale_path_checks, gating=True),
    _Family("live-sources", run_live_source_checks, gating=True),
    _Family("runtime-contract", run_runtime_contract_checks, gating=True),
    _Family("root-hygiene", run_root_hygiene_checks, gating=True),
    _Family("agentica-root-hygiene", run_agentica_root_hygiene_checks,
            gating=True, label_key="name"),
    _Family("archive-boundaries", run_archive_boundary_checks, gating=True),
    _Family("meditation-timestamps", _run_meditation_timestamp_checks, gating=False),
    # WARN-only family: a schema violation is the observation A3 is collecting,
    # so it must never gate the exit code (see _run_schema_violation_checks).
    _Family("schema-violations", _run_schema_violation_checks, gating=False),
    _Family("local-llm", _run_local_llm_checks, gating=False),
    # WARN-only like local-llm: a dead Qdrant/Ollama must be VISIBLE here, but doctor
    # itself does not gate on it -- fleet_probe.py's own 30-min banner is the alarm.
    _Family("container-services", _run_container_service_checks, gating=False),
    # WARN-only like local-llm: a dead audit gate must be VISIBLE here, but the
    # remediation pipeline itself already fails closed on it (exit!=0 rejects).
    _Family("audit-gate-canary", _run_audit_gate_canary_checks, gating=False),
    # GATE, not spectator: a silent SessionEnd-emitter death ran 15 days undetected
    # in June 2026. A stale stream FAILs doctor (affects exit code), never just WARNs.
    _Family("telemetry-freshness", run_telemetry_freshness_checks, gating=True),
    # claude-telemetry is a gating family: its FAIL feeds the exit code, unlike
    # the WARN-only meditation-timestamps / local-llm probes.
    _Family("claude-telemetry", _run_claude_telemetry_checks, gating=True, warns=False),
    # GATE, not spectator: a broken chain means a past remediation verdict was edited
    # after the fact, which invalidates every efficacy number derived from it.
    _Family("exec-chain", _run_exec_chain_checks, gating=True),
    # WARN-only like local-llm: the factory substrate must be VISIBLE here, but the
    # factory is not live yet — nothing to gate (see _run_factory_checks).
    _Family("factory", _run_factory_checks, gating=False),
    # Claude architecture score — the enforcement pack's live verdict on the
    # ~/.claude runtime, folded in as a GATE (a zeroed category FAILs doctor).
    # This is the "100/100 becomes continuously enforced, not a one-time
    # judgment" contract from the claude verifier backlog's Definition of Done.
    _Family("claude-architecture", run_claude_arch_checks, gating=True),
)


def run_families(families: tuple[_Family, ...] = CHECK_FAMILIES) -> tuple[list[str], dict, int]:
    """Run every family in order. Returns (printable lines, counts, exit code).

    Separated from main() so the order/counting/exit contract is assertable
    without capturing stdout.
    """
    lines: list[str] = []
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    exit_code = 0

    for family in families:
        results = family.runner()
        for result in results:
            label = result[family.label_key]
            lines.append(f"[{result['status']}] {label}: {result['detail']}")
            status = result["status"]
            if status == "OK":
                counts["OK"] += 1
            elif status == "WARN" and family.warns:
                counts["WARN"] += 1
            elif status == "FAIL" and family.gating:
                counts["FAIL"] += 1
                exit_code = 1

    return lines, counts, exit_code


def main() -> int:
    print("Order Samurai Doctor")
    print("--------------------")

    lines, counts, exit_code = run_families()
    for line in lines:
        print(line)

    print("--------------------")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())