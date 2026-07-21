#!/usr/bin/env python3
"""Security-gate canary WRITER — Mac rebuild of the lost ~/.claude/scripts/security_gate_canary.py.

The Windows original ran monthly (Claude_Security_Gate_Canary task) and self-tested the
skill security gate by scanning the known-malicious fixture, expecting the gate to BLOCK
it (exit 2). That scripts tier was never in git and died in the Windows→Mac migration;
this port lives next to the surviving producers in agentica_core (not ~/.claude/scripts).

On the Mac the production scanner is agentica_core.verify_secrets (the Sword-pillar
secret scanner). The self-test runs it in a subprocess against
~/.claude/tests/canary_fixtures/malicious_skill_fixture and keeps the historical
exit-code contract the fixture itself documents:

    2 = fixture detected and blocked  (gate working)
    0 = fixture NOT detected          (gate regressed — the one failure that matters)
    1 = scanner crashed               (import error, traceback — also a fault)

Writes (read side: Order Samurai/bin/canary_fault_detect.py and
agentica_core/scouts security_signals → Gate_Canary_Fault):
  ~/.claude/data/security_gate_canary.json      current state {last_run, gate_working,
                                                expected_exit, actual_exit, max_age_days, ...}
  ~/.claude/data/security_gate_canary.jsonl.gz  append-only history, historical record shape

A missing fixture or missing scanner is a FAULT (gate_working: false), never a pass.
Exit code: 0 when the gate passed its self-test, 1 on any fault.
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GOVERNANCE_ROOT = Path(__file__).resolve().parents[2]
SCANNER_FILE = GOVERNANCE_ROOT / "agentica_core" / "verify_secrets.py"
SCANNER_NAME = "agentica_core.verify_secrets"

DEFAULT_FIXTURE = Path.home() / ".claude" / "tests" / "canary_fixtures" / "malicious_skill_fixture"
DEFAULT_OUT = Path.home() / ".claude" / "data" / "security_gate_canary.json"
DEFAULT_HISTORY = Path.home() / ".claude" / "data" / "security_gate_canary.jsonl.gz"

EXPECTED_EXIT = 2
# Monthly cadence + slack; consumed by canary_fault_detect.py and the sword scout,
# which otherwise default to 7 days and would flag a monthly canary as stale.
MAX_AGE_DAYS = 35
SCAN_TIMEOUT_S = 120
_EXCERPT_LIMIT = 300

# Runs inside the subprocess so the self-test exercises the real interpreter + import
# path of the production scanner, not an already-imported copy in this process.
_SELF_TEST_SNIPPET = """\
import sys
from pathlib import Path
from agentica_core.verify_secrets import run_checks
results = run_checks([Path(sys.argv[1])])
for r in results:
    print(f"[{r['status']}] {r['label']}: {r['detail']}")
sys.exit(2 if any(r["status"] == "FAIL" for r in results) else 0)
"""


def run_self_test(fixture: Path) -> tuple[int | None, str]:
    """Run the scanner against the fixture. Returns (actual_exit, stderr_excerpt).

    actual_exit is None when the self-test could not run at all (missing fixture,
    missing scanner, timeout) — always a fault, with the reason in the excerpt.
    """
    if not fixture.exists():
        return None, f"fixture missing: {fixture}"
    if not SCANNER_FILE.exists():
        return None, f"scanner missing: {SCANNER_FILE}"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _SELF_TEST_SNIPPET, str(fixture)],
            cwd=GOVERNANCE_ROOT,
            capture_output=True,
            text=True,
            timeout=SCAN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, f"scanner timed out after {SCAN_TIMEOUT_S}s"
    return proc.returncode, proc.stderr.strip()[:_EXCERPT_LIMIT]


def write_canary(fixture: Path, out: Path, history: Path) -> dict:
    actual_exit, excerpt = run_self_test(fixture)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = {
        "last_run": now,
        "gate_working": actual_exit == EXPECTED_EXIT,
        "expected_exit": EXPECTED_EXIT,
        "actual_exit": actual_exit,
        "max_age_days": MAX_AGE_DAYS,
        "scanner": SCANNER_NAME,
        "fixture": str(fixture),
        "stderr_excerpt": excerpt,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    record = {k: state[k] for k in
              ("expected_exit", "actual_exit", "gate_working", "stderr_excerpt")}
    with gzip.open(history, "at", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": now, **record}) + "\n")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Security-gate canary writer (monthly self-test)")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE,
                        help="malicious skill fixture directory")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="path to security_gate_canary.json")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY,
                        help="path to security_gate_canary.jsonl.gz")
    args = parser.parse_args(argv)

    state = write_canary(args.fixture, args.out, args.history)
    if state["gate_working"]:
        print(f"✅ Security gate canary PASSED (scanner exited {EXPECTED_EXIT} as expected).")
        return 0
    reason = state["stderr_excerpt"] or f"scanner exited {state['actual_exit']}, expected {EXPECTED_EXIT}"
    print(f"❌ Security gate canary FAILED: {reason}")
    print("   Do NOT regenerate blindly — run /canary-fault-diagnosis (gate-not-working).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
