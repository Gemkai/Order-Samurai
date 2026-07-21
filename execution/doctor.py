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
from execution.verify_no_stale_paths import run_checks as run_stale_path_checks
from execution.verify_no_stale_paths import summarize as summarize_stale_path_checks
from execution.verify_live_sources import run_checks as run_live_source_checks
from execution.verify_live_sources import summarize as summarize_live_source_checks
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
    # Recoverable: a backstop run of stamp_dojo_timestamps.py can fill these.
    recoverable = [i.get("id", "?") for i in backlog
                   if (i.get("status") == "done" and not i.get("completed_at"))
                   or (i.get("status") == "doing" and not i.get("started_at"))]
    # Lost: a done item with no started_at has no honest source for it (commit-span
    # is not work-duration; stamping it = a fabricated 0-min sample). The real fix is
    # forward — stamp started_at at dispatch, not only at cycle end.
    lost = [i.get("id", "?") for i in backlog
            if i.get("status") == "done" and not i.get("started_at")]
    results: list[dict] = []
    if recoverable:
        results.append({"status": "WARN", "label": "dojo-timestamps",
                        "detail": f"{len(recoverable)} item(s) missing recoverable timestamps "
                                  f"({', '.join(recoverable[:5])}) — run bin/stamp_dojo_timestamps.py"})
    if lost:
        results.append({"status": "WARN", "label": "dojo-timestamps.lost-samples",
                        "detail": f"{len(lost)} done item(s) missing started_at with no recoverable source "
                                  f"({', '.join(lost[:5])}) — calibration samples permanently lost; fix "
                                  f"forward capture (stamp started_at at dispatch, not only at cycle end)"})
    if not results:
        results.append({"status": "OK", "label": "dojo-timestamps",
                        "detail": "all done/doing backlog items carry calibration timestamps"})
    return results


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


def main() -> int:
    print("Order Samurai Doctor")
    print("--------------------")

    path_results = run_path_authority_checks()
    for result in path_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    stale_results = run_stale_path_checks()
    for result in stale_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    live_source_results = run_live_source_checks()
    for result in live_source_results:
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

    local_llm_results = _run_local_llm_checks()
    for result in local_llm_results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")

    path_counts, path_exit = summarize_path_authority_checks(path_results)
    stale_counts, stale_exit = summarize_stale_path_checks(stale_results)
    live_source_counts, live_source_exit = summarize_live_source_checks(live_source_results)
    runtime_counts, runtime_exit = summarize_runtime_contract_checks(runtime_results)
    root_counts, root_exit = summarize_root_hygiene_checks(root_results)
    archive_counts, archive_exit = summarize_archive_boundary_checks(archive_results)

    dojo_ts_warn = sum(1 for r in dojo_ts_results if r["status"] == "WARN")
    dojo_ts_ok = sum(1 for r in dojo_ts_results if r["status"] == "OK")

    local_llm_warn = sum(1 for r in local_llm_results if r["status"] == "WARN")
    local_llm_ok = sum(1 for r in local_llm_results if r["status"] == "OK")

    total_ok = path_counts["OK"] + stale_counts["OK"] + live_source_counts["OK"] + runtime_counts["OK"] + root_counts["OK"] + archive_counts["OK"] + dojo_ts_ok + local_llm_ok
    total_warn = path_counts["WARN"] + stale_counts["WARN"] + live_source_counts["WARN"] + runtime_counts["WARN"] + root_counts["WARN"] + archive_counts["WARN"] + dojo_ts_warn + local_llm_warn
    total_fail = path_counts["FAIL"] + stale_counts["FAIL"] + live_source_counts["FAIL"] + runtime_counts["FAIL"] + root_counts["FAIL"] + archive_counts["FAIL"]
    exit_code = 1 if path_exit or stale_exit or live_source_exit or runtime_exit or root_exit or archive_exit else 0

    print("--------------------")
    print(f"Summary: OK={total_ok} WARN={total_warn} FAIL={total_fail}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())