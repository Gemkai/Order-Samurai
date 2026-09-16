from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, NamedTuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import re

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
from execution.activation_drift import run_checks as run_activation_drift_checks
from execution.escalation_sla import run_checks as run_escalation_sla_checks
from execution.shared_checkout_health import run_checks as run_shared_checkout_checks
from execution.scheduled_run_outcomes import run_checks as run_scheduled_run_outcome_checks
from execution.verify_incident_coverage import run_doctor_checks as run_incident_coverage_checks
from execution.verify_open_pr_health import run_checks as run_open_pr_health_checks


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


def _parse_violation_ts(raw):
    """A schema-violation timestamp, or None when the value cannot be read as one.

    Returns None rather than raising so a malformed timestamp downgrades one row's
    dating instead of aborting the family — "ts is not a string" is one of the
    violations this sink exists to record, so its rows must stay countable.

    A tz-naive value is graded UTC, the same rule the clean-since stamp already uses.
    Without it a naive `instance.ts` produced a naive datetime that later compared
    against the aware stamp and raised TypeError out of this family — the FIRST in
    doctor's registry, so the whole run aborted and, under the launchd job, the state
    file was never written.
    """
    from datetime import datetime, timezone
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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

    A violation is identified by the INSTANCE it describes, not by the row that
    recorded it. reflex-engine re-validates its history on every API restart and
    re-appends a row for each pre-existing bad record, so one 2026-08-06 SENSEI_LEDGER
    row had been re-recorded 14 times, most recently 2026-08-29 — and reading the row
    `ts` restarted the 7-day streak on every restart. A3 was therefore unflippable from
    2026-07-27 onward while no NEW violation had occurred since 08-07 (audit B4).
    So: date each violation by `instance.ts` where the row carries one, and count a
    re-observation of an instance already seen only once.
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
    reobservations = 0
    seen: set[tuple] = set()
    if sink_path.exists():
        try:
            for line in sink_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                # A JSON scalar or array is valid JSON and not a row. Without this the
                # row.get() below raises AttributeError, and since this is the FIRST
                # family in the registry that aborts every later check in the run —
                # and, under the launchd job, write_state with it.
                if not isinstance(row, dict):
                    continue
                instance = row.get("instance")
                instance = instance if isinstance(instance, dict) else {}
                # When the row names the instance it observed, that instance's own ts
                # is when the violation HAPPENED; the row ts is merely when a restart
                # noticed it again. An UNPARSEABLE instance.ts falls back to the row ts
                # rather than dropping the row: "ts is not a string" is itself a
                # violation this sink records, so discarding those rows would report a
                # clean streak precisely when a real violation had just been observed.
                ts = _parse_violation_ts(instance.get("ts"))
                dateable_instance = ts is not None
                if ts is None:
                    ts = _parse_violation_ts(row.get("ts"))
                if ts is None:
                    continue
                # De-duplicate ONLY on a row whose instance carries a parseable
                # timestamp — that is what makes two rows the same occurrence. The key
                # is the whole instance plus the violation list, not (sink, ts,
                # reflex_id): the live sink holds several distinct rows sharing one
                # (ts, reflex_id) pair, so the narrow key would report them as one.
                # Startup re-observations re-serialise an identical instance, so the
                # wide key still folds exactly the rows it should.
                if dateable_instance:
                    identity = (row.get("sink"),
                                json.dumps(instance, sort_keys=True, default=str),
                                json.dumps(row.get("violations"), sort_keys=True,
                                           default=str))
                    if identity in seen:
                        reobservations += 1
                        continue
                    seen.add(identity)
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
        # Report the streak honestly at both ends. Once it passes 7d the blocker is no
        # longer elapsed time but the stale stamp, and saying "cannot flip until it
        # reaches 7d" at 26d would be the same kind of untrue health claim this
        # family exists to prevent.
        gate = ("A3 cannot flip until it reaches 7d" if days < 7 else
                f"already past the 7d gate — A3 is blocked on {stamp_path.name} still "
                f"reading {since.date()}, not on elapsed time")
        seen_note = (f", {reobservations} re-observation(s) of an already-counted instance"
                     if reobservations else "")
        return [{"status": "WARN", "label": label,
                 "detail": f"{violations} distinct violation(s) recorded{seen_note}; newest at "
                           f"{reset_by.isoformat()} reset the streak to {days:.1f}d — {gate}"}]
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


def _run_injection_hook_checks(hook_path: Path | None = None,
                               node_bin: str | None = None) -> list[dict]:
    """WARN when the read-injection-scanner hook is missing or broken.

    The hook (~/.claude/hooks/gsd-read-injection-scanner.js) is the PostToolUse
    guard that flags prompt-injection phrases in Read/Grep/Glob results. Its
    pytest suite (tests/test_read_injection_scanner.py) is marked live_machine
    -- deselected in CI, covered only by verify.sh, which nothing runs on a
    schedule. Until 2026-08-21 that meant the hook could vanish or break and
    NOTHING automated would notice: the suite's own hook-exists canary only
    fires when a human runs it. This probe is that canary's scheduled home --
    doctor runs nightly via the dojo daemon-health gate.

    Smoke test, not a scan: feed one benign PostToolUse payload and require
    exit 0 with no stderr crash. The hook's contract is never-blocks (exit 0
    always), so ANY nonzero exit means it is broken, not strict. Full behavior
    (fires on injected content, exclusions, severity tiers) stays with the
    live_machine suite. Fix: reinstall the hook from its source (see
    docs/handoffs/STAGED-control-plane-change-2026-08-09-read-injection-content-scan.md).
    """
    import shutil as _shutil
    import subprocess as _subprocess

    hook = hook_path or (Path.home() / ".claude" / "hooks" / "gsd-read-injection-scanner.js")
    if not hook.is_file():
        return [{"status": "WARN", "label": "injection-hook",
                 "detail": f"read-injection-scanner hook missing at {hook} -- Read/Grep/"
                           f"Glob results are NOT being scanned for prompt-injection "
                           f"phrases; reinstall the hook (its live_machine test suite "
                           f"only runs via verify.sh and cannot catch this on its own)"}]
    node = node_bin or _shutil.which("node")
    if node is None:
        return [{"status": "WARN", "label": "injection-hook",
                 "detail": f"hook present at {hook} but node is not on PATH -- the hook "
                           f"cannot execute, so the scan is silently dead"}]
    payload = ('{"tool_name":"Read","tool_input":{"file_path":"/tmp/doctor-canary.md"},'
               '"tool_response":"def add(a, b):\\n    return a + b\\n"}')
    try:
        proc = _subprocess.run([node, str(hook)], input=payload, capture_output=True,
                               text=True, timeout=10)
    except Exception as exc:
        return [{"status": "WARN", "label": "injection-hook",
                 "detail": f"hook smoke-run failed to execute ({exc.__class__.__name__}: "
                           f"{exc}) -- the scan is silently dead"}]
    if proc.returncode != 0:
        return [{"status": "WARN", "label": "injection-hook",
                 "detail": f"hook exited {proc.returncode} on a benign payload (contract "
                           f"is never-block/exit-0) -- it is broken, and PostToolUse "
                           f"hooks that error are skipped silently: "
                           f"{(proc.stderr or '').strip()[:200]}"}]
    return [{"status": "OK", "label": "injection-hook",
             "detail": f"hook present and exits 0 on benign input ({hook})"}]


def _run_dep_scanner_presence_checks(finder=None) -> list[dict]:
    """WARN when pip-audit is not importable under doctor's own interpreter.

    Order Samurai's pyproject.toml declares `pip-audit>=2.7`, but the AgenticaOS
    dev venv installs from the monorepo's requirements-dev.txt rather than that
    package's own pyproject -- the two can drift apart (requirements-dev.txt
    gained its own pip-audit pin on 2026-08-02, `75543600`, precisely to keep
    them aligned, but a venv installed before that commit, or never
    reinstalled since, is still silently missing the scanner). When that
    happens, bin/codebase_deps_audit.py correctly detects it and reports
    scanner_errors {'pip_audit': 'did not produce valid JSON'}, refusing to
    claim zero vulnerabilities -- but that detection only fires when someone
    actually runs the deps audit. This probe surfaces the same gap on every
    doctor run, without invoking a full scan. Fix: `pip install -r
    requirements-dev.txt` (or `pip install pip-audit` directly) into the
    active venv.
    """
    import importlib.util

    find = finder or importlib.util.find_spec
    try:
        spec = find("pip_audit")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return [{"status": "WARN", "label": "dep-scanner-presence",
                 "detail": "pip-audit not importable under this interpreter "
                           f"({sys.executable}) -- bin/codebase_deps_audit.py "
                           "will silently report scanner_errors and skip "
                           "vulnerability scanning unless someone runs it "
                           "directly; pip install -r requirements-dev.txt to fix"}]
    return [{"status": "OK", "label": "dep-scanner-presence",
             "detail": "pip-audit importable"}]


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


def _run_exec_chain_checks(
    api_dir: Path | None = None,
    runner: Callable[..., object] | None = None,
) -> list[dict]:
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
    import os
    import subprocess

    api_dir = api_dir or (Path(__file__).resolve().parents[2] / "api")
    if not (api_dir / "src" / "verify-chain-cli.ts").exists():
        # This walks UP to ../api. Outside an Agentica tree that path is somebody's
        # home directory, and printing it as "verifier missing at <absolute path>"
        # reads as a broken install rather than a check that does not apply here
        # (audit 2026-09-01, finding B3). Inside the repo the same row is a real
        # signal — the toolchain is genuinely absent — so only the wording changes.
        from execution.claude_runtime_target import is_standalone_distribution
        if is_standalone_distribution():
            return [{"status": "WARN", "label": "exec-chain.not-applicable-outside-agentica",
                     "detail": "the chain verifier lives in the Agentica api/ package "
                               "(one implementation, never re-derived in Python), which "
                               "a standalone distribution does not ship -- exec_log "
                               "tamper-evidence is unverifiable here by design"}]
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": f"verifier missing at {api_dir}/src/verify-chain-cli.ts -- "
                           f"exec_log tamper-evidence is unverified"}]

    # Bare npx may wait on the registry when dependencies are missing. Only the
    # pinned local binary has an auditable version.
    tsx_name = "tsx.cmd" if os.name == "nt" else "tsx"
    tsx = api_dir / "node_modules" / ".bin" / tsx_name
    if not tsx.is_file():
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": f"local tsx runtime missing at {tsx} -- exec_log "
                           f"tamper-evidence is unverified (install Governance/api "
                           f"dependencies, then re-run doctor)"}]

    run = runner or subprocess.run
    try:
        proc = run(
            [str(tsx), "src/verify-chain-cli.ts"],
            cwd=str(api_dir), capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return [{"status": "WARN", "label": "exec-chain",
                 "detail": f"local tsx runtime could not start at {tsx} -- exec_log "
                           f"tamper-evidence is unverified"}]
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
        from execution.claude_runtime_target import runtime_root as _claude_home
        if not _claude_home().exists():
            # No Claude runtime home on this machine at all (a CI runner; a fresh
            # clone on a host that has never run Claude Code): the adapter refuses
            # to resolve the platform, and rightly so -- but that is "nothing to
            # measure", not the dead-emitter outage this gate exists for. WARN,
            # matching verify_telemetry_freshness and verify_claude_root_hygiene's
            # absent-runtime-root rows. An EXISTING ~/.claude with no telemetry
            # file still FAILs below. An injected `source` keeps exact semantics.
            return [{"status": "WARN", "label": "claude-telemetry",
                     "detail": f"no Claude runtime home at {_claude_home()}; telemetry "
                               f"liveness cannot be measured on this machine"}]
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


def _run_claude_hook_health_checks(quarantine_path: "Path | None" = None,
                                   timings_path: "Path | None" = None,
                                   registry: "dict | None" = None,
                                   now=None,
                                   window_hours: float = 24.0,
                                   fail_share: float = 0.05) -> list[dict]:
    """FAIL/WARN on quarantined ~/.claude hooks — silence that reads as health.

    hook_circuit_breaker.py quarantines a hook after consecutive failures, and
    hook_dispatch.py then skips it silently: every dispatch still "fires" and
    logs `status: quarantined`, so the hook's own output simply stops. Before
    2026-09-02 nothing outside the dispatcher read data/hook_quarantine.json —
    17 advisory hooks (mechanism-audit, service-liveness-check, doc-parity-check,
    the quota guards, ...) sat quarantined for a week, no-oping 100% of their
    dispatches, while doctor reported FAIL=0 (three-month incident coverage
    review, R0). This family is the missing consumer.

    Rows:
      * one row per quarantined hook — FAIL when the registry says its
        criticality is "blocking" (a security gate that is silently off), WARN
        otherwise; each names since-when, the failure count, and how many
        dispatches it no-oped in the window;
      * FAIL when quarantined dispatches exceed `fail_share` of all dispatches
        in the window (the fleet-wide silence case, whatever the criticality);
      * WARN, never OK, when the telemetry or quarantine file cannot be read —
        "could not measure" must not print as clean.

    `quarantine_path` / `timings_path` / `registry` / `now` are injectable so
    the contract is testable without the live ~/.claude.
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    home = Path.home() / ".claude"
    quarantine_path = quarantine_path or home / "data" / "hook_quarantine.json"
    timings_path = timings_path or home / "data" / "hook_timings.jsonl"
    label = "claude-hook-health"

    if registry is None:
        scripts = str(home / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        try:
            import hook_registry as _hr  # type: ignore
            registry = dict(_hr.HOOKS)
        except Exception:
            registry = {}

    if not timings_path.is_file():
        return [{"status": "WARN", "label": label,
                 "detail": f"no hook telemetry at {timings_path} — cannot tell whether "
                           f"any hook is quarantined (dispatcher has never logged here)"}]

    quarantined: dict = {}
    if quarantine_path.is_file():
        try:
            data = _json.loads(quarantine_path.read_text(encoding="utf-8"))
            quarantined = data.get("quarantined") or {}
            if not isinstance(quarantined, dict):
                raise ValueError("quarantined is not an object")
        except Exception as exc:
            return [{"status": "WARN", "label": label,
                     "detail": f"{quarantine_path} unreadable ({exc}) — quarantine state "
                               f"cannot be measured; treat every advisory hook as suspect"}]

    now = now or _dt.now(_tz.utc)
    cutoff = now.timestamp() - window_hours * 3600.0
    total = 0
    noops: dict[str, int] = {}
    try:
        size = timings_path.stat().st_size
        with timings_path.open("rb") as fh:
            if size > 4_000_000:          # bounded tail: the file grows ~100k rows/day
                fh.seek(size - 4_000_000)
                fh.readline()
            for raw in fh.read().decode("utf-8", errors="replace").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = _json.loads(raw)
                    ts = _dt.fromisoformat(str(row.get("ts")))
                except Exception:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_tz.utc)
                if ts.timestamp() < cutoff:
                    continue
                total += 1
                if row.get("status") == "quarantined":
                    noops[row.get("hook", "?")] = noops.get(row.get("hook", "?"), 0) + 1
    except Exception as exc:
        return [{"status": "WARN", "label": label,
                 "detail": f"{timings_path} unreadable ({exc}) — quarantine impact "
                           f"cannot be measured"}]

    if not quarantined:
        return [{"status": "OK", "label": label,
                 "detail": f"0 hooks quarantined; {total} dispatches in the last "
                           f"{window_hours:.0f}h"}]

    rows: list[dict] = []
    for hook_id, info in sorted(quarantined.items()):
        crit = str((registry.get(hook_id) or {}).get("criticality") or "unknown")
        since = info.get("quarantined_at")
        try:
            since_s = _dt.fromtimestamp(int(since), _tz.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            since_s = "unknown"
        status = "FAIL" if crit == "blocking" else "WARN"
        rows.append({"status": status, "label": f"{label}.{hook_id}",
                     "detail": f"quarantined since {since_s} ({crit}; "
                               f"{info.get('consecutive_failures', '?')} consecutive fails, "
                               f"{info.get('probe_failures', 0)} failed probes) — "
                               f"no-oped {noops.get(hook_id, 0)} dispatches in "
                               f"{window_hours:.0f}h; release: "
                               f"~/.claude/scripts/hook_circuit_breaker.py --release "
                               f"{hook_id}, then dispatch it once"})
    quarantined_dispatches = sum(noops.values())
    share = (quarantined_dispatches / total) if total else 0.0
    if total and share > fail_share:
        rows.append({"status": "FAIL", "label": f"{label}.share",
                     "detail": f"{quarantined_dispatches} of {total} dispatches in the last "
                               f"{window_hours:.0f}h were quarantine no-ops "
                               f"({share:.0%} > {fail_share:.0%}) — the hook layer is "
                               f"largely silent, not healthy"})
    else:
        rows.append({"status": "WARN", "label": f"{label}.share",
                     "detail": f"{len(quarantined)} hook(s) quarantined; {quarantined_dispatches} "
                               f"of {total} dispatches no-oped ({share:.0%})"})
    return rows


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
    # Three of these four walk UP out of this tree — ../.planning/GOAL_REGISTRY.jsonl,
    # ../Execution/factory/ledger.py — and so assume an Agentica-shaped parent. In a
    # standalone distribution that parent is somebody's home directory: the checks then
    # reported "unreadable: No such file or directory" with an absolute path, which
    # reads as a broken install rather than as a check that does not apply here
    # (audit 2026-09-01, finding B3). Say so once, plainly, instead.
    from execution.claude_runtime_target import is_standalone_distribution
    if is_standalone_distribution():
        return [{
            "status": "WARN", "label": "factory.not-applicable-outside-agentica",
            "detail": "queue, plist-drift and ledger-chain all read paths above this "
                      "tree (.planning/, Execution/factory/, ~/Library/LaunchAgents); "
                      "this is a standalone distribution with no Agentica parent, so "
                      "they are skipped rather than reported as missing files",
        }]
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


#: Labels whose installed plist is knowingly unsourced. See the file's own rationale.
_LAUNCHD_SOURCE_POLICY = ROOT_DIR / "config" / "launchd_source_policy.json"


#: `--` is illegal inside an XML comment. plutil -lint accepts it, a strict parser
#: does not, and several installed plists carry it (e.g. a documented `--flag`).
_XML_COMMENT_RE = re.compile(rb"<!--.*?-->", re.S)


def _plist_program_args(plist: Path) -> list[str] | None:
    """The argv strings an installed job runs, or None when the file will not parse.

    Comments are stripped before parsing: a plist whose only defect is `--` inside a
    comment must not fall out of the source audit, because that would let anyone make
    a hand-installed job invisible to the very check that exists to find one.
    """
    import plistlib
    import xml.parsers.expat
    try:
        raw = plist.read_bytes()
    except OSError:
        return None
    for candidate in (raw, _XML_COMMENT_RE.sub(b"", raw)):
        try:
            data = plistlib.loads(candidate)
        except (ValueError, xml.parsers.expat.ExpatError):
            continue
        args = data.get("ProgramArguments")
        if not isinstance(args, list):
            program = data.get("Program")
            args = [program] if isinstance(program, str) else []
        return [a for a in args if isinstance(a, str)]
    return None


def _names_this_repo_elsewhere(args: list[str], root: Path) -> bool:
    """True when an argv path is this repo's layout at a DIFFERENT location on disk.

    Distinguishes the two ways a job can be out of scope. A job running
    /opt/other/tool.py has nothing to do with this repo. A job running
    /Users/x/AgenticaOS/Governance/bin/y.sh while doctor runs from a worktree is this
    repo, at the deployment — and reporting that as "nothing to audit, all clean" is
    the false-OK the reverse check exists to avoid.
    """
    for arg in args:
        for token in arg.replace("'", " ").replace('"', " ").split():
            parts = [p for p in token.split("/") if p]
            for start in range(len(parts) - 1):
                tail = Path(*parts[start:])
                candidate = root / tail
                if candidate.exists() or candidate.parent.is_dir():
                    return True
    return False


def _installed_runs_repo_code(plist: Path, root: Path) -> bool | None:
    """True when the installed job executes a file inside `root`; None if unreadable.

    plistlib, not a grep over the file: a plist is structured data, and matching the
    repo path anywhere in it would also fire on a WorkingDirectory, a log path, or a
    comment, none of which mean the job runs this repo's code.

    Within argv the match is a substring, not a prefix, and covers the `$HOME`/`~`
    spellings. Two live jobs (`conductor-prep`, `sensei-cycle`) reach their script
    through `bash -c '… exec "$HOME/<repo>/…"'`, so a prefix test on whole argv
    elements silently excluded them from the audit.
    """
    args = _plist_program_args(plist)
    if args is None:
        return None
    needles = [str(root.resolve()) + "/"]
    try:
        rel = root.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        rel = None
    if rel:
        needles += [f"$HOME/{rel}/", f"~/{rel}/", "${HOME}/" + rel + "/"]
    return any(needle in a for a in args for needle in needles)


def _acknowledged_unsourced(policy_path: Path | None = None) -> tuple[dict, str | None]:
    """The owner-triaged exemption set, and a reason string when it could not be read."""
    import json
    path = policy_path or _LAUNCHD_SOURCE_POLICY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"{path.name} missing"
    except (OSError, ValueError) as exc:
        return {}, f"{path.name} unreadable: {exc}"
    labels = (raw.get("acknowledged_unsourced") or {}).get("labels")
    if not isinstance(labels, dict):
        return {}, f"{path.name} has no acknowledged_unsourced.labels object"
    return labels, None


def _factory_plist_drift_checks(root: Path, installed_dir: Path,
                                policy_path: Path | None = None) -> list[dict]:
    """Both directions of the repo <-> launchd relationship.

    Forward — the documented failure: the repo plist was edited, launchd still runs
    the stale installed copy.

    Reverse (audit B5) — an installed job runs this repo's code with NO source plist
    committed, so it exists only on one machine and nothing reviews or reproduces it.
    Five Order Samurai jobs were in that state and the forward-only comparison could
    not see any of them: it iterates SOURCES, so a plist with no source is invisible
    by construction. Labels the owner has triaged out of scope are counted and named
    rather than dropped — a silent exemption would rebuild the same blind spot.
    """
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

    # -- installed-without-source (the reverse direction) --------------------------
    source_names = {src.name for src in sources}
    acknowledged, policy_error = _acknowledged_unsourced(policy_path)
    in_scope, unsourced, acknowledged_seen, unparseable = 0, [], [], []
    elsewhere = 0
    for installed in sorted(installed_dir.glob("com.agentica.*.plist")):
        args = _plist_program_args(installed)
        if args is None:
            unparseable.append(installed.stem)
            continue
        if not _installed_runs_repo_code(installed, root):
            if _names_this_repo_elsewhere(args, root):
                elsewhere += 1
            continue
        in_scope += 1
        if installed.name in source_names:
            continue
        label = installed.stem
        (acknowledged_seen if label in acknowledged else unsourced).append(label)

    if policy_error:
        results.append({"status": "WARN", "label": "factory.plist-unsourced",
                        "detail": f"cannot read the acknowledged-unsourced policy "
                                  f"({policy_error}) — every unsourced plist below is "
                                  f"reported without it"})
    if unparseable:
        results.append({"status": "WARN", "label": "factory.plist-unsourced",
                        "detail": f"{len(unparseable)} installed plist(s) could not be "
                                  f"parsed even with comments stripped, so an unaudited "
                                  f"job may be running repo code: "
                                  f"{', '.join(unparseable)}"})
    for label in unsourced:
        results.append({"status": "WARN", "label": "factory.plist-unsourced",
                        "detail": f"{label} is installed and runs code from this repo but "
                                  f"has no source plist under Governance/automation/launchd "
                                  f"or Governance/bin — it exists on this machine only"})

    # Unconditional, not folded into the OK row: the exemption set has to stay legible
    # in exactly the run where something else is already failing, which is when an
    # operator is actually reading this output.
    if acknowledged_seen:
        results.append({"status": "OK", "label": "factory.plist-acknowledged",
                        "detail": f"{len(acknowledged_seen)} unsourced plist(s) "
                                  f"owner-acknowledged in "
                                  f"{(policy_path or _LAUNCHD_SOURCE_POLICY).name}: "
                                  f"{', '.join(acknowledged_seen)}"})
    # An acknowledgement for a job that is no longer installed is a standing
    # pre-approval for whatever next claims that label.
    # Gated on in_scope: when no installed plist names this checkout at all, nothing
    # about the acknowledgement set has been observed and "not installed" would be an
    # artefact of the checkout rather than a finding.
    stale_ack = sorted(set(acknowledged) - set(acknowledged_seen)) if in_scope else []
    if stale_ack:
        results.append({"status": "WARN", "label": "factory.plist-acknowledged",
                        "detail": f"{len(stale_ack)} acknowledged label(s) are not "
                                  f"installed and pre-approve whatever next claims them: "
                                  f"{', '.join(stale_ack)}"})

    if not unsourced:
        if in_scope == 0 and elsewhere:
            # NOT an OK. Nothing was audited, and "0 … all sourced" is indistinguishable
            # from "audited 52, all clean" — the same shape as the finding this check
            # exists to close. The installed plists name the DEPLOYMENT path, so any
            # checkout that is not the deployment audits nothing.
            results.append({"status": "WARN", "label": "factory.plist-unsourced",
                            "detail": f"not applicable from this checkout: "
                                      f"{elsewhere} installed plist(s) run this repo's "
                                      f"layout at another location, none under {root} — "
                                      f"run doctor from the deployment checkout for this "
                                      f"check to mean anything"})
        else:
            results.append({"status": "OK", "label": "factory.plist-unsourced",
                            "detail": f"{in_scope} installed plist(s) run repo code, "
                                      f"all sourced"})
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
    # WARN-only like local-llm: a missing pip-audit is currently VISIBLE only
    # when someone runs bin/codebase_deps_audit.py directly (IMPSYS-55); this
    # surfaces the same gap on every doctor run without gating on it -- the
    # deps-audit script itself already fails closed (scanner_errors, no
    # false "zero vulnerabilities" claim).
    _Family("dep-scanner-presence", _run_dep_scanner_presence_checks, gating=False),
    # WARN-only like local-llm: a missing/broken injection-scanner hook must be
    # VISIBLE nightly (its live_machine pytest suite only runs via manual
    # verify.sh), but a security-scan outage should page the operator, not
    # brick every doctor-gated pipeline.
    _Family("injection-hook", _run_injection_hook_checks, gating=False),
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
    # GATE: a quarantined hook is a mechanism that silently stopped; nothing else
    # reads hook_quarantine.json (coverage review 2026-09-02, R0.1).
    _Family("claude-hook-health", _run_claude_hook_health_checks, gating=True),
    # GATE, not spectator: a broken chain means a past remediation verdict was edited
    # after the fact, which invalidates every efficacy number derived from it.
    _Family("exec-chain", _run_exec_chain_checks, gating=True),
    # WARN-only like local-llm: the factory substrate must be VISIBLE here, but the
    # factory is not live yet — nothing to gate (see _run_factory_checks).
    _Family("factory", _run_factory_checks, gating=False),
    # WARN-only (coverage review R5, 2026-09-02): "landed means live". A running service
    # older than its source, a sub-bundle gitlink that pins a commit the checkout left, or
    # a main checkout 100+ commits behind is exactly how three correct fixes sat inert this
    # quarter. Spectator for its first week; becomes a gate once the live tree is clean.
    _Family("activation-drift", run_activation_drift_checks, gating=False),
    # WARN-only (coverage review R4.3): every open escalation carries a date. One row per
    # overdue backlog row / undated or past-due goal / long-expired HITL item, so the WARN
    # count is the count of decisions owed. Mutations go through each file's owner.
    _Family("escalation-sla", run_escalation_sla_checks, gating=False),
    # WARN-only, spectator (2026-09-04): a stale, CI-red or conflicted open GitHub PR on
    # this repo was previously invisible to every local automation here -- a cloud
    # session's own PR check-in loop is the only thing that ever saw it. Bridges via `gh`
    # CLI when present/authenticated; WARNs "cannot verify" (never a synthetic OK, and
    # not ERROR -- that would make this spectator gate every machine without `gh`).
    _Family("open-pr-health", run_open_pr_health_checks, gating=False),
    # GATE (coverage review R2.1): the multi-writer main checkout. Only one probe can
    # FAIL — a git-TRACKED file under Order Samurai's state/ modified by a machine
    # writer, the 08-14 stash-loss class — and that must reach the M1 banner. Stranded
    # branches, old stashes, unpushed commits and materialized files are WARN rows.
    _Family("shared-checkout-health", run_shared_checkout_checks, gating=True),
    # WARN-only (coverage review R1.1/R1.2, Sol 5.6): did each scheduled job produce the
    # artifact it promised? Spectator for its first week: the rows already distinguish a
    # broken outcome (FAIL) from a missing measurement (WARN). Promote after one clean week.
    _Family("scheduled-run-outcomes", run_scheduled_run_outcome_checks, gating=False),
    # Advisory by design (coverage review R11, Sol 5.6): every incident doc and recent
    # memory note must name the instrument that would catch it again, or say why not.
    # The uncovered set IS the backlog this family exposes; the top families by count.
    _Family("incident-coverage", run_incident_coverage_checks, gating=False),
    # Claude architecture score — the enforcement pack's live verdict on the
    # ~/.claude runtime, folded in as a GATE (a zeroed category FAILs doctor).
    # This is the "100/100 becomes continuously enforced, not a one-time
    # judgment" contract from the claude verifier backlog's Definition of Done.
    _Family("claude-architecture", run_claude_arch_checks, gating=True),
)


class DoctorReport(NamedTuple):
    """One doctor run, in the shape a consumer other than a human reading stdout needs.

    `lines` is the printed transcript; `fails`, `warns`, and `errors` retain every
    non-OK observation with its original status and family gating policy. `counts`
    and `exit_code` remain the gate verdict: a spectator FAIL is observable without
    silently becoming a blocking failure.
    """
    lines: list[str]
    counts: dict
    exit_code: int
    fails: list[dict]
    #: Families that could not run at all. Distinct from `fails`: a consumer that
    #: pages on a FAIL must also know when a check produced no verdict.
    errors: list[dict] = []
    warns: list[dict] = []


def run_report(families: tuple[_Family, ...] | None = None) -> DoctorReport:
    """Run every family in order and return the full structured result.

    Separated from main() so the order/counting/exit contract is assertable
    without capturing stdout. `families` is resolved at call time, not bound as a
    default, so a caller (or a test) that replaces the registry is actually obeyed.
    """
    families = CHECK_FAMILIES if families is None else families
    lines: list[str] = []
    counts = {"OK": 0, "WARN": 0, "FAIL": 0, "ERROR": 0}
    exit_code = 0
    fails: list[dict] = []
    errors: list[dict] = []
    warns: list[dict] = []

    for family in families:
        try:
            results = family.runner()
        except Exception as exc:  # noqa: BLE001 - a crashing family is a finding, not a stop
            # Crash isolation, matching cli.py's long-standing behaviour (audit S2).
            # A family that raised used to abort the whole run, so one broken check
            # took every LATER check with it and doctor reported nothing at all —
            # and under the launchd job, --write-state never ran either. The row is
            # ERROR, not FAIL: doctor made no measurement for this family, and
            # "this check failed" would be a verdict nobody computed.
            results = [{
                "status": "ERROR",
                family.label_key: f"{family.name}.family-crashed",
                "detail": f"{type(exc).__name__}: {exc}",
            }]
        for result in results:
            label = result[family.label_key]
            lines.append(f"[{result['status']}] {label}: {result['detail']}")
            status = result["status"]
            if status == "OK":
                counts["OK"] += 1
            elif status == "WARN":
                # Recorded for every family, counted only for the ones whose WARNs
                # count (the two non-uniform rules the registry tests pin).
                warns.append({"family": family.name, "label": label,
                              "detail": result["detail"], "status": status,
                              "gating": family.gating})
                if family.warns:
                    counts["WARN"] += 1
            elif status == "ERROR":
                # Counted for every family, gating or not: "could not run" is never a
                # spectator's private business — it is the reason a row is missing.
                counts["ERROR"] += 1
                errors.append({"family": family.name, "label": label,
                               "detail": result["detail"], "status": status,
                               "gating": family.gating})
            elif status == "FAIL":
                fails.append({"family": family.name, "label": label,
                              "detail": result["detail"], "status": status,
                              "gating": family.gating})
                if family.gating:
                    counts["FAIL"] += 1
                    exit_code = 1

    # FAIL outranks ERROR, matching verifier_results.summarize().
    if exit_code == 0 and counts["ERROR"]:
        exit_code = 2

    return DoctorReport(lines, counts, exit_code, fails, errors, warns)


def run_families(families: tuple[_Family, ...] | None = None) -> tuple[list[str], dict, int]:
    """Back-compatible view of run_report: (printable lines, counts, exit code)."""
    report = run_report(families)
    return report.lines, report.counts, report.exit_code


def _current_umask() -> int:
    """The process umask, read without leaving it changed.

    os.umask is the only way to read it, and it is a set-and-return call.
    """
    import os as _os
    value = _os.umask(0o022)
    _os.umask(value)
    return value


#: Where --write-state records the run. Gitignored (state/*.json): this is a
#: generated observation, not tracked truth.
STATE_PATH = ROOT_DIR / "state" / "doctor_last.json"


def write_state(report: DoctorReport, path: Path | None = None, now=None) -> Path:
    """Record the run so something OTHER than a human at a terminal can read it.

    doctor reported FAIL=1 continuously from 2026-08-23 and nothing noticed, because
    its only surface was stdout on a manual invocation (audit B2). This file is that
    missing surface: bin/hitl_alerts.py reads it on the existing 30-minute notify and
    daily digest carriers. The producer lives HERE, in the script that owns the
    numbers, rather than in the plist — a shell redirect in a plist is not a producer
    anything can be tested against.

    Written atomically (temp + replace) so a reader can never observe a half-file and
    mistake a truncated FAIL list for a clean run.

    The temp file gets a UNIQUE name in the same directory. A fixed `.tmp` sibling was
    safe against torn reads but not against concurrent writers: two overlapping runs
    shared one temp path, so a FAIL run could write its temp, have a clean run
    overwrite it, then publish the clean payload and report success — losing the FAIL
    while claiming to have recorded it. Doctor has a daily job, a manual entry point
    and a sensei-cycle caller, so overlap is reachable.
    """
    import json
    import os
    import tempfile
    from datetime import datetime, timezone

    path = path or STATE_PATH
    now = now or datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(),
        "exit_code": report.exit_code,
        "counts": report.counts,
        "fails": report.fails,
        # Distinct from fails on purpose: a consumer that pages on a FAIL must also
        # learn when a check produced no verdict at all (audit S1/S2).
        "errors": report.errors,
        # Every WARN row too (family/label/detail): the dashboard's operator-attention
        # panel and the spectator families depend on it. hitl_alerts keeps reading
        # only `fails` for its banner — a WARN is never a banner.
        "warns": report.warns,
        "producer": "Governance/Order Samurai/execution/doctor.py --write-state",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        # mkstemp creates 0600. Every other file under state/ is world-readable, and
        # this one is a health report with no secrets; leaving it owner-only would be
        # an unannounced narrowing introduced by the temp-file fix, not a decision.
        os.chmod(tmp_name, 0o644 & ~_current_umask())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave the scratch file behind on a failed write.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def main(argv: list[str] | None = None) -> int:
    # argparse BEFORE any check runs: doctor used to execute its full sweep on
    # `--help` (audit B11), so probing the command mutated state.
    import argparse
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="Order Samurai health check. Exit 0 = no gating FAIL, 1 = at least one.",
    )
    parser.add_argument(
        "--write-state", action="store_true",
        help=f"also record the run as JSON at {STATE_PATH.name} (summary counts + every "
             f"FAIL row) so scheduled consumers can alert on it",
    )
    args = parser.parse_args(argv)

    print("Order Samurai Doctor")
    print("--------------------")

    report = run_report()
    for line in report.lines:
        print(line)

    print("--------------------")
    counts = report.counts
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    if args.write_state:
        # A failure to record must never change the verdict doctor just computed.
        try:
            print(f"state: wrote {write_state(report)}")
        except OSError as exc:
            print(f"state: FAILED to write {STATE_PATH}: {exc}", file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
