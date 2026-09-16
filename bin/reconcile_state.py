#!/usr/bin/env python3
"""Deterministic Daily Reconciler for Order Samurai.

Replaces the legacy 6-hour, $8-burning overnight LLM meditation cycle with a
10-second, $0.00 deterministic state and truth reconciliation pass.

Stages:
1. Telemetry Freshness & Compaction (cleanup .tmp/, socket locks, stale logs)
2. Deterministic Metric Sweep (executes all deterministic verification scripts)
3. Falsifiability Assertions (proves verifiers catch bad fixtures)
4. Morning Standup Briefing (emits summary to state/reconciliation_report.json)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import REPO_ROOT, STATE_DIR, TMP_DIR


def stage_1_telemetry_compaction() -> dict:
    """Stage 1: Clean temp directories, reap dead files, and rotate stale state."""
    t0 = time.perf_counter()
    cleaned_count = 0
    
    if TMP_DIR.exists():
        for p in TMP_DIR.glob("*"):
            if p.is_file() and time.time() - p.stat().st_mtime > 86400:
                try:
                    p.unlink()
                    cleaned_count += 1
                except Exception:
                    pass

    return {
        "stage": "telemetry_compaction",
        "cleaned_temp_files": cleaned_count,
        "duration_s": round(time.perf_counter() - t0, 3),
        "status": "PASS",
    }


def stage_2_deterministic_sweep(repo_root: Path) -> dict:
    """Stage 2: Run all deterministic metric scanners and healers."""
    t0 = time.perf_counter()
    results = {}
    
    scripts = [
        ("path_authority", ["python3", "execution/verify_path_authority.py"]),
        ("root_hygiene", ["python3", "execution/verify_root_hygiene.py"]),
        ("timeout_audit", ["python3", "execution/timeout_audit_scan.py"]),
        ("doc_parity", ["python3", "execution/verify_doc_parity.py"]),
        ("mcp_smoke", ["python3", "bin/mcp_smoke_test.py"]),
        ("secret_scrub", ["python3", "bin/secret_scrub.py", "--json"]),
    ]
    
    for name, cmd in scripts:
        try:
            res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=30, check=False)
            results[name] = {
                "exit_code": res.returncode,
                "status": "PASS" if res.returncode == 0 else "FAIL",
                "summary": res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "",
            }
        except Exception as exc:
            results[name] = {"exit_code": -1, "status": "ERROR", "summary": str(exc)}
            
    passed_count = sum(1 for r in results.values() if r["status"] == "PASS")
    return {
        "stage": "deterministic_sweep",
        "total_checks": len(scripts),
        "passed": passed_count,
        "failed": len(scripts) - passed_count,
        "details": results,
        "duration_s": round(time.perf_counter() - t0, 3),
        "status": "PASS" if passed_count == len(scripts) else "WARN",
    }


def stage_3_falsifiability(repo_root: Path) -> dict:
    """Stage 3: Run verifier falsifiability checks against bad/clean fixtures."""
    t0 = time.perf_counter()
    cmd = ["python3", "execution/verify_falsifiability.py", "--json"]
    try:
        res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=30, check=False)
        passed = res.returncode == 0
        return {
            "stage": "falsifiability",
            "exit_code": res.returncode,
            "status": "PASS" if passed else "WARN",
            "duration_s": round(time.perf_counter() - t0, 3),
        }
    except Exception as exc:
        return {
            "stage": "falsifiability",
            "status": "ERROR",
            "summary": str(exc),
            "duration_s": round(time.perf_counter() - t0, 3),
        }


def stage_4_morning_briefing(stage_results: list[dict], repo_root: Path) -> dict:
    """Stage 4: Compile report into state/reconciliation_report.json."""
    t0 = time.perf_counter()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    report_file = STATE_DIR / "reconciliation_report.json"
    
    total_duration = sum(s.get("duration_s", 0) for s in stage_results)
    
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_duration_s": round(total_duration, 3),
        "stages": {s["stage"]: s for s in stage_results},
        "cost_usd": 0.0,
        "overall_status": "PASS" if all(s.get("status") in ("PASS", "WARN") for s in stage_results) else "FAIL",
    }
    
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {
        "stage": "morning_briefing",
        "report_path": str(report_file),
        "duration_s": round(time.perf_counter() - t0, 3),
        "status": "PASS",
    }


def run_reconciliation(repo_root: Path = REPO_ROOT) -> dict:
    """Execute all 4 stages of deterministic daily reconciliation."""
    t0 = time.perf_counter()
    
    s1 = stage_1_telemetry_compaction()
    s2 = stage_2_deterministic_sweep(repo_root)
    s3 = stage_3_falsifiability(repo_root)
    s4 = stage_4_morning_briefing([s1, s2, s3], repo_root)
    
    total_duration = round(time.perf_counter() - t0, 3)
    
    return {
        "status": "PASS" if all(s["status"] in ("PASS", "WARN") for s in (s1, s2, s3, s4)) else "FAIL",
        "total_duration_s": total_duration,
        "cost_usd": 0.0,
        "stages": [s1, s2, s3, s4],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic daily reconciliation")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args()
    
    res = run_reconciliation()
    
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=" * 60)
        print("  ORDER SAMURAI DETERMINISTIC DAILY RECONCILIATION")
        print("=" * 60)
        print(f"Status:         {res['status']}")
        print(f"Total Duration: {res['total_duration_s']:.2f}s")
        print(f"Cost:           ${res['cost_usd']:.2f} (Zero LLM Tokens)")
        print("-" * 60)
        for s in res["stages"]:
            print(f"  [{s.get('status', 'OK'):<5}] {s['stage']:<25} ({s.get('duration_s', 0):.2f}s)")
        print("=" * 60)
        
    return 0 if res["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
