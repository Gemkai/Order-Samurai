#!/usr/bin/env python3
"""injection_guard_canary — prove the prompt-injection guard still fires, and say which half of it.

Chain 13 (Prompt Injection) is the most differentiated thing Order Samurai detects, and the hook
protecting it has nothing testing it in production. A guard that has silently stopped detecting and
a guard with nothing to detect produce identical output: no kill-chain events. Only one of those is
good news. This probe distinguishes them, the way
``agentica_core/doctor_audits/security_gate_canary.py`` already does for the secret scanner.

The guard has two stages and they fail differently, so they are reported separately:

  ENFORCEMENT   A BLOCK_PATTERN match yields confidence 1.0 and the hook exits 2. This is the ONLY
                stage that blocks anything. If an obvious injection walks through, the guard is
                dead and Chain 13 is decorative.

  SEMANTIC      A SUSPICIOUS pattern escalates to a local-LLM call. Note the ceiling on this stage:
                a semantic confirmation yields confidence 0.8, which is appended to
                kill_chain_events.jsonl with ``remediation_action: "logged"`` -- and then ALLOWED.
                Only 1.0 blocks. The semantic stage is telemetry, never enforcement, even when it
                is perfectly healthy.

  BENIGN        Clean input must pass. Over-blocking is a real failure mode: a guard that cries
                wolf gets disabled by whoever it annoys, and a disabled guard blocks nothing.

The semantic stage fails open by construction -- ``check_semantic_score()`` wraps its HTTP call in
a bare ``except Exception: return False``, so an unreachable backend is indistinguishable from a
clean verdict. That is why this probe checks reachability DIRECTLY rather than inferring it from
the hook's exit code: the exit code cannot tell you, and that is the whole problem.

The probe corpus lives in ``tests/fixtures/injection_probes.json``, base64-encoded. A canary corpus
that trips the guard it tests cannot be authored or committed otherwise; encoding attack fixtures
is the long-standing convention for exactly this reason.

``--guard <path>`` scores a CANDIDATE guard script instead of the live one -- for the injection-guard
autoresearch pilot (self_harness_cycle.py's future pattern lane), never for monitoring production.
When given, the held-out benign corpus (``tests/fixtures/injection_probes_heldout_benign.json``) is
also scored: benign text never used to tune a candidate, several items deliberately adjacent to the
guard's SUSPICIOUS_PATTERNS, so a candidate that overfits the training-visible benign set (e.g. by
broadening to block anything containing "override") is caught here instead of shipping. ``--guard``
without an explicit ``--out`` refuses to run, to make it structurally impossible for a candidate's
score to land in the live guard's canary state file (the file ``canary_fault_detect.py`` reads).

Exit codes:
  0  enforcement stage working (semantic degradation is REPORTED but does not fail the probe,
     because the semantic stage cannot block even when healthy -- use --strict to change that)
  1  FAULT: an injection walked through, a benign input was blocked, the corpus is missing, or
     the guard is absent

Usage:
  python3 bin/injection_guard_canary.py            # probe + write state
  python3 bin/injection_guard_canary.py --json     # machine-readable state on stdout
  python3 bin/injection_guard_canary.py --strict   # semantic degradation also exits 1
  python3 bin/injection_guard_canary.py --guard candidate.py --out /tmp/candidate.json
                                                    # score a candidate guard, never touches live state
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GUARD = Path.home() / ".claude" / "hooks" / "prompt_injection_guard.py"
PROBES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "injection_probes.json"
HELDOUT_BENIGN = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "injection_probes_heldout_benign.json"
)
DEFAULT_OUT = Path.home() / ".claude" / "data" / "injection_guard_canary.json"
DEFAULT_HISTORY = Path.home() / ".claude" / "data" / "injection_guard_canary.jsonl.gz"

# Matches security_gate_canary.py so canary_fault_detect.py's staleness classifier agrees.
MAX_AGE_DAYS = 7

EXIT_BLOCKED = 2
EXIT_ALLOWED = 0
HOOK_TIMEOUT_S = 20.0
SEMANTIC_PROBE_TIMEOUT_S = 4.0
SEMANTIC_IMPORT_TIMEOUT_S = 5.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_probes(path: Path = PROBES) -> dict[str, list[str]]:
    """Decode the base64 probe corpus. Raises OSError/ValueError -- both are FAULTs, never passes."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("encoding") != "base64":
        raise ValueError(f"unsupported probe encoding: {raw.get('encoding')!r}")
    out = {}
    for stage in ("enforcement", "semantic", "benign"):
        out[stage] = [base64.b64decode(v).decode("utf-8") for v in raw.get(stage, [])]
    if not out["enforcement"]:
        raise ValueError("probe corpus has no enforcement probes; the canary would always pass")
    return out


def load_heldout_benign(path: Path = HELDOUT_BENIGN) -> list[str]:
    """Decode the held-out benign corpus. Raises OSError/ValueError like ``load_probes``.

    Disjoint from ``load_probes()``'s benign[] on purpose -- see the module docstring. Only ever
    consulted when ``--guard`` is scoring a candidate; the live guard's monitoring run never
    touches this.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("encoding") != "base64":
        raise ValueError(f"unsupported probe encoding: {raw.get('encoding')!r}")
    items = [base64.b64decode(v).decode("utf-8") for v in raw.get("benign", [])]
    if not items:
        raise ValueError("held-out benign corpus is empty; the control would prove nothing")
    return items


def _run_guard(text: str, guard: Path | None = None) -> tuple[int, str]:
    """Feed *text* through *guard* (default: the module-level ``GUARD``) exactly as Claude Code
    would. Returns (exit, stderr).

    ``guard`` defaults to ``None`` rather than the module constant directly so tests that
    monkeypatch ``canary.GUARD`` after import still take effect -- a ``guard: Path = GUARD``
    default is bound once at import time and would silently ignore the monkeypatch.
    """
    guard = guard if guard is not None else GUARD
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": text}})
    try:
        proc = subprocess.run(
            [sys.executable, str(guard)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT_S,
        )
        return proc.returncode, (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, f"hook did not return within {HOOK_TIMEOUT_S}s"
    except OSError as exc:
        return -1, f"could not execute hook: {exc}"


def semantic_endpoint(guard: Path | None = None) -> str:
    """The URL the guard's semantic stage actually calls, computed by importing the guard's module.

    Imports rather than regexes the source: as of the 2026-08-06 Ollama migration the endpoint is a
    runtime value (``SEMANTIC_HOST + "/api/chat"``, OLLAMA_HOST-normalized and loopback-gated), not
    a single string literal a regex can pattern-match — so the old ``url = "http://..."`` scrape
    matched nothing and reported the semantic stage permanently degraded. Importing preserves the
    "cannot drift from the file under test" guarantee the regex was for, while surviving changes to
    how the guard CONSTRUCTS the endpoint. A bare ``SEMANTIC_ENDPOINT = None`` (non-loopback
    OLLAMA_HOST) prints as the empty string, which correctly falls through to "unknown" below —
    the guard disabled its own semantic stage too.

    ``guard`` defaults to ``None``, not the module constant, for the same monkeypatch reason as
    ``_run_guard``.
    """
    guard = guard if guard is not None else GUARD
    try:
        proc = subprocess.run(
            [
                sys.executable, "-c",
                "import importlib.util, sys\n"
                "spec = importlib.util.spec_from_file_location('_injection_guard_probe', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(m)\n"
                "print(getattr(m, 'SEMANTIC_ENDPOINT', '') or '')\n",
                str(guard),
            ],
            capture_output=True,
            text=True,
            timeout=SEMANTIC_IMPORT_TIMEOUT_S,
        )
        return (proc.stdout or "").strip()
    except Exception:  # noqa: BLE001 - any failure to resolve it means "unknown", never a crash
        return ""


def probe_semantic_endpoint(url: str) -> tuple[str, str]:
    """Reachability of the semantic backend. Returns (status, detail).

    The hook cannot report this itself: its ``except Exception: return False`` makes an unreachable
    backend look exactly like a clean verdict.
    """
    if not url:
        return "unknown", "could not determine the semantic endpoint from the guard source"

    # Probe the server root, not the chat route: the question is whether anything is listening,
    # not whether one particular model is loaded.
    base = url.split("/v1/")[0].split("/api/")[0]
    try:
        with urllib.request.urlopen(base, timeout=SEMANTIC_PROBE_TIMEOUT_S) as response:
            return "live", f"{base} responded HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        # An HTTP error still proves something is listening.
        return "live", f"{base} responded HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - any failure to reach it IS the finding
        return "unreachable", f"{base} unreachable ({type(exc).__name__}); semantic stage fails open"


def run_probes(
    probes: dict,
    guard: Path | None = None,
    heldout_benign: list[str] | None = None,
    heldout_error: str | None = None,
) -> dict:
    """Score *guard* (default: the live one) against *probes*.

    ``heldout_benign``/``heldout_error`` are populated by ``main()`` only when ``--guard`` is
    scoring a candidate -- see the module docstring for why this control exists and why it never
    runs against the live guard's own monitoring pass.
    """
    guard = guard if guard is not None else GUARD
    results: list[dict] = []

    for text in probes["enforcement"]:
        code, stderr = _run_guard(text, guard)
        results.append(
            {
                "stage": "enforcement",
                "probe": text[:80],
                "expected_exit": EXIT_BLOCKED,
                "actual_exit": code,
                "passed": code == EXIT_BLOCKED,
                "stderr_excerpt": stderr[:200],
            }
        )

    for text in probes["benign"]:
        code, stderr = _run_guard(text, guard)
        results.append(
            {
                "stage": "benign",
                "probe": text[:80],
                "expected_exit": EXIT_ALLOWED,
                "actual_exit": code,
                "passed": code == EXIT_ALLOWED,
                "stderr_excerpt": stderr[:200],
            }
        )

    # Semantic probes are recorded but never gate `gate_working`: by design this stage logs at 0.8
    # and allows, so exit 0 here is correct behaviour rather than evidence of a pass.
    for text in probes["semantic"]:
        code, stderr = _run_guard(text, guard)
        results.append(
            {
                "stage": "semantic",
                "probe": text[:80],
                "expected_exit": EXIT_ALLOWED,
                "actual_exit": code,
                "passed": code in (EXIT_ALLOWED, EXIT_BLOCKED),
                "note": "semantic stage logs at confidence 0.8 and allows; it never blocks",
                "stderr_excerpt": stderr[:200],
            }
        )

    heldout_results: list[dict] | None = None
    if heldout_benign is not None:
        heldout_results = []
        for text in heldout_benign:
            code, stderr = _run_guard(text, guard)
            heldout_results.append(
                {
                    "stage": "heldout_benign",
                    "probe": text[:80],
                    "expected_exit": EXIT_ALLOWED,
                    "actual_exit": code,
                    "passed": code == EXIT_ALLOWED,
                    "stderr_excerpt": stderr[:200],
                }
            )

    endpoint = semantic_endpoint(guard)
    semantic_status, semantic_detail = probe_semantic_endpoint(endpoint)

    enforcement = [r for r in results if r["stage"] == "enforcement"]
    benign = [r for r in results if r["stage"] == "benign"]
    missed = [r for r in enforcement if not r["passed"]]
    false_positives = [r for r in benign if not r["passed"]]
    heldout_false_positives = (
        [r for r in heldout_results if not r["passed"]] if heldout_results is not None else []
    )

    # Anti-reward-hacking: a candidate that over-blocks the held-out set is not gate_working,
    # exactly like the training-visible benign set -- passing only the corpus it can see proves
    # nothing about a candidate that was tuned against that same corpus.
    gate_working = not missed and not false_positives and not heldout_false_positives

    state = {
        "last_run": _now(),
        "gate_working": gate_working,
        "max_age_days": MAX_AGE_DAYS,
        "guard_path": str(guard),
        "guard_present": guard.exists(),
        "enforcement_total": len(enforcement),
        "enforcement_blocked": sum(1 for r in enforcement if r["passed"]),
        "injections_missed": len(missed),
        "benign_total": len(benign),
        "benign_false_positives": len(false_positives),
        "semantic_endpoint": endpoint,
        "semantic_status": semantic_status,
        "semantic_detail": semantic_detail,
        "semantic_degraded": semantic_status != "live",
        "results": results,
    }

    if heldout_error:
        # A missing/broken held-out corpus is informational only -- it must never fault a
        # candidate scoring run just because the control set itself is unavailable, the same
        # fail-open-but-report posture as the semantic reachability check above.
        state["heldout_status"] = "unavailable"
        state["heldout_error"] = heldout_error
    elif heldout_results is not None:
        state["heldout_status"] = "scored"
        state["heldout_benign_total"] = len(heldout_results)
        state["heldout_benign_false_positives"] = len(heldout_false_positives)
        state["heldout_results"] = heldout_results

    return state


def _fault_state(reason: str, guard: Path | None = None) -> dict:
    """A canary that cannot run is a FAULT, never a pass."""
    guard = guard if guard is not None else GUARD
    return {
        "last_run": _now(),
        "gate_working": False,
        "max_age_days": MAX_AGE_DAYS,
        "guard_path": str(guard),
        "guard_present": guard.exists(),
        "fault_reason": reason,
        "semantic_status": "unknown",
        "semantic_degraded": True,
        "results": [],
    }


def write_state(state: dict, out: Path, history: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    record = {k: state.get(k) for k in (
        "last_run", "gate_working", "enforcement_total", "enforcement_blocked",
        "injections_missed", "benign_false_positives", "semantic_status",
    )}
    history.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(history, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt-injection guard canary.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--probes", type=Path, default=PROBES)
    parser.add_argument(
        "--guard", type=Path, default=None,
        help="score a CANDIDATE guard script instead of the live one (autoresearch pilot only; "
             "requires --out, see below)",
    )
    parser.add_argument(
        "--heldout", type=Path, default=HELDOUT_BENIGN,
        help="held-out benign corpus, only scored when --guard is given",
    )
    parser.add_argument("--json", action="store_true", help="print the full state to stdout")
    parser.add_argument(
        "--strict", action="store_true",
        help="also exit non-zero when the semantic backend is unreachable",
    )
    parser.add_argument("--no-write", action="store_true", help="probe only; do not write state")
    args = parser.parse_args()

    # --guard scores a candidate, never the live guard's own health signal -- refuse to let a
    # candidate's score silently land in DEFAULT_OUT, the file canary_fault_detect.py reads to
    # decide whether the PRODUCTION guard is working.
    if args.guard is not None and args.out == DEFAULT_OUT and not args.no_write:
        parser.error(
            "--guard requires --out <path> (or --no-write) -- refusing to overwrite the live "
            "guard's canary state with a candidate's score"
        )

    guard = args.guard if args.guard is not None else GUARD

    if not guard.exists():
        state = _fault_state(f"guard not found at {guard}", guard)
    else:
        try:
            probes = load_probes(args.probes)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            state = _fault_state(f"probe corpus unusable: {type(exc).__name__}: {exc}", guard)
        else:
            heldout_benign = None
            heldout_error = None
            if args.guard is not None:
                try:
                    heldout_benign = load_heldout_benign(args.heldout)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    heldout_error = f"{type(exc).__name__}: {exc}"
            state = run_probes(probes, guard=guard, heldout_benign=heldout_benign, heldout_error=heldout_error)

    if not args.no_write:
        write_state(state, args.out, args.history)

    if args.json:
        print(json.dumps(state, indent=2))
    else:
        print("Injection Guard Canary")
        print("----------------------")
        if state.get("fault_reason"):
            print(f"FAULT: {state['fault_reason']}")
        else:
            print(f"Enforcement : {state['enforcement_blocked']}/{state['enforcement_total']} blocked")
            print(f"Benign      : {state['benign_false_positives']}/{state['benign_total']} false positive(s)")
            print(f"Semantic    : {state['semantic_status']} -- {state['semantic_detail']}")
            if state.get("heldout_status") == "scored":
                print(
                    f"Held-out    : {state['heldout_benign_false_positives']}/"
                    f"{state['heldout_benign_total']} false positive(s)"
                )
            elif state.get("heldout_status") == "unavailable":
                print(f"Held-out    : unavailable -- {state['heldout_error']}")
            for result in state["results"]:
                if result["stage"] == "enforcement" and not result["passed"]:
                    print(f"  MISSED (exit {result['actual_exit']}): {result['probe']}")
                if result["stage"] == "benign" and not result["passed"]:
                    print(f"  OVER-BLOCKED (exit {result['actual_exit']}): {result['probe']}")
            for result in state.get("heldout_results") or []:
                if not result["passed"]:
                    print(f"  OVER-BLOCKED (held-out, exit {result['actual_exit']}): {result['probe']}")
        print("----------------------")
        print(f"gate_working={state['gate_working']} semantic_degraded={state['semantic_degraded']}")

    if not state["gate_working"]:
        return 1
    if args.strict and state["semantic_degraded"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
