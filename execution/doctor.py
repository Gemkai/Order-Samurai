from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verify_archive_boundaries import run_checks as run_archive_boundary_checks
from execution.verify_archive_boundaries import summarize as summarize_archive_boundary_checks
from execution.verify_path_authority import run_checks as run_path_authority_checks
from execution.verify_path_authority import summarize as summarize_path_authority_checks
from execution.verify_root_hygiene import run_checks as run_root_hygiene_checks
from execution.verify_root_hygiene import summarize as summarize_root_hygiene_checks
from execution.verify_runtime_contract import run_checks as run_runtime_contract_checks
from execution.verify_runtime_contract import summarize as summarize_runtime_contract_checks


def _run_dojo_timestamp_checks() -> list[dict]:
    """WARN on done/doing backlog items missing their calibration timestamps.

    Calibration coefficients only accumulate from (started_at, completed_at)
    pairs — an unstamped done item is a silently lost sample.
    Fix: python bin/stamp_dojo_timestamps.py
    """
    import json
    state = ROOT_DIR / "state" / "DOJO_STATE.json"
    if not state.exists():
        return []
    try:
        backlog = json.loads(state.read_text(encoding="utf-8")).get("backlog", [])
    except Exception as exc:
        return [{"status": "WARN", "label": "dojo-timestamps",
                 "detail": f"DOJO_STATE.json unreadable: {exc}"}]
    missing = [i.get("id", "?") for i in backlog
               if (i.get("status") == "done" and not i.get("completed_at"))
               or (i.get("status") == "doing" and not i.get("started_at"))]
    if missing:
        return [{"status": "WARN", "label": "dojo-timestamps",
                 "detail": f"{len(missing)} item(s) missing calibration timestamps "
                           f"({', '.join(missing[:5])}) — run bin/stamp_dojo_timestamps.py"}]
    return [{"status": "OK", "label": "dojo-timestamps",
             "detail": "all done/doing backlog items carry calibration timestamps"}]


def main() -> int:
    print("Order Samurai Doctor")
    print("--------------------")

    path_results = run_path_authority_checks()
    for result in path_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    runtime_results = run_runtime_contract_checks()
    for result in runtime_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    root_results = run_root_hygiene_checks()
    for result in root_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    archive_results = run_archive_boundary_checks()
    for result in archive_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    dojo_ts_results = _run_dojo_timestamp_checks()
    for result in dojo_ts_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    path_counts, path_exit = summarize_path_authority_checks(path_results)
    runtime_counts, runtime_exit = summarize_runtime_contract_checks(runtime_results)
    root_counts, root_exit = summarize_root_hygiene_checks(root_results)
    archive_counts, archive_exit = summarize_archive_boundary_checks(archive_results)

    dojo_ts_warn = sum(1 for r in dojo_ts_results if r["status"] == "WARN")
    dojo_ts_ok = sum(1 for r in dojo_ts_results if r["status"] == "OK")

    total_ok = path_counts["OK"] + runtime_counts["OK"] + root_counts["OK"] + archive_counts["OK"] + dojo_ts_ok
    total_warn = path_counts["WARN"] + runtime_counts["WARN"] + root_counts["WARN"] + archive_counts["WARN"] + dojo_ts_warn
    total_fail = path_counts["FAIL"] + runtime_counts["FAIL"] + root_counts["FAIL"] + archive_counts["FAIL"]
    exit_code = 1 if path_exit or runtime_exit or root_exit or archive_exit else 0

    print("--------------------")
    print(f"Summary: OK={total_ok} WARN={total_warn} FAIL={total_fail}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())