"""Order Samurai metric aggregator — FROZEN kernel, QUARANTINED reducers.

FROZEN repo-local kernel (2026-06-12), merged into this package from the Order
Samurai repo. The live dashboard kernel is aggregate.py in this same package.

WARNING (2026-07-12): the reducer bodies still defined below are FROZEN,
pre-fix implementations that predate the aggregate.py correctness sweeps. The
only supported surface of this module is the REGISTRY *declarations* (metric /
pillar / source / tier), consumed by execution/verify_live_sources.py and the
kernel-drift tests. Do NOT wire new executors to these reducers — known
pre-fix semantics that remain:
  - _ratio_by_field returns 0.0 on empty input, so "no data" reads as a real
    measurement (the live kernel's r_local_routing returns None instead);
  - _governance_pass_rate returns 0.0 on import failure, so an infra failure
    reads as governance collapse.

ORPHAN RESOLUTION (2026-07-12): the 15 metrics this REGISTRY declared but the
live kernel never emitted (test_kernel_drift.py's former
KNOWN_ORPHANS_PENDING_DECISION) were worked through with evidence:
  - MCP_vs_CLI_Ratio re-registered in aggregate.py with corrected semantics
    (the frozen reducer counted the literal value "mcp", which canonical
    telemetry never emits — real vocabulary is cli/mixed/none);
  - the other 14 deleted: concept already live under another name
    (Backlog_Velocity->Complexity_Weighted_Throughput, Documentation_Parity_
    Latency->Doc_Parity_Issues, Principle_Violations->Rule_Violations,
    Secret_Scrub_Count->Secret_Scrubs, Subagent_Cost_Multiplier->Subagent_
    Efficiency_Index, Opus_Share->payload tier_mix, Metric_Live_Fraction->
    Instrumentation_Coverage, Daily_Ronin_Spend->Total_Cost family,
    Zombie_Process_Count->Agent_Process_Count), dead source (Security_Score,
    Secret_Scrub_Count files absent; Tool_Failure_Rate's tool_latencies field
    never emitted; hook_failure events stopped 2026-06), or superseded
    mechanism (Ronin_Cycle_Success_Rate -> Self_Correction_Rate /
    Mechanism_Liveness over exec_log; Vibe_Alignment lives on as a component
    of Estimated_Human_Time_Saved per the 2026-07-08 arts consolidation).

Reducers that DO have a corrected counterpart in aggregate.py are no longer
defined here at all — they are re-exported from aggregate.py (see the import
block below), so the corrected semantics have exactly one home.

REGISTRY: declares each metric's real source — never invented.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — agentica_core may be imported stand-alone or from the repo
# root.  We add the repo root to sys.path so execution.* verifiers are
# importable without hard-coding an absolute path.
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Re-exports from the live kernel — the single home of corrected reducer
# semantics (disrupt-action filtering, data_gap honesty, calibration gates).
# The frozen duplicates these replace had silently drifted; keeping only a
# re-export makes drift structurally impossible (test_kernel_drift.py asserts
# identity). verify_live_sources.run_checks already imports aggregate for the
# payload build, so this adds no new import weight for the live consumer.
# ---------------------------------------------------------------------------
from agentica_core.aggregate import (  # noqa: E402
    _calibrate_coefficients,
    _estimated_agent_time_saved,
    _estimated_cost_savings,
    _estimated_human_time_saved,
    _kill_chains_disrupted,
    _parse_iso,
    _pending_chain_proposals,
)


# ---------------------------------------------------------------------------
# Telemetry loader
# ---------------------------------------------------------------------------

def load_telemetry_records(repo_root: Path) -> list[dict]:
    """Read all state/logs/*.json files and return a flat list of record dicts.

    Each log file may be:
      - JSON Lines  (one JSON object per line, as the harness emits)
      - A JSON array  (legacy or future format)

    Records that are not dicts are silently skipped.
    Files that fail to parse are silently skipped (log format may change).
    """
    logs_dir = repo_root / "state" / "logs"
    if not logs_dir.exists():
        return []

    records: list[dict] = []
    for log_path in sorted(logs_dir.glob("*.json")):
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if not text.strip():
            continue

        # Try JSON array first, then JSON Lines.
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    records.extend(item for item in parsed if isinstance(item, dict))
                    continue
            except json.JSONDecodeError:
                pass

        # JSON Lines
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                continue

    return records


# ---------------------------------------------------------------------------
# Verifier-backed reducers (Root_Hygiene_Issues, Hardcoded_Path_Incidents)
# These call the real verifier functions so the metric reads from the live
# source, not from telemetry logs.
# ---------------------------------------------------------------------------

def _count_root_hygiene_fails(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    from execution.verify_root_hygiene import run_checks
    results = run_checks(repo_root=repo_root)
    return sum(1 for r in results if r.get("status") == "FAIL")


def _count_hardcoded_path_fails(records: list[dict], repo_root: Path) -> int:  # noqa: ARG001
    from execution.verify_path_authority import run_checks
    results = run_checks(repo_root=repo_root)
    return sum(1 for r in results if r.get("status") == "FAIL")


# ---------------------------------------------------------------------------
# Telemetry-backed reducers
# ---------------------------------------------------------------------------

def _ratio_by_field(records: list[dict], field: str, value: str) -> float:
    """Fraction of records where `field == value`, among those that have the field set."""
    values = [r.get(field) for r in records if r.get(field) is not None]
    if not values:
        return 0.0
    return sum(1 for v in values if v == value) / len(values)


def _local_routing_share(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    return _ratio_by_field(records, "model_tier", "LOCAL")


# _skill_promotions / _loop_breaker_fires (and their _read_jsonl/_count_jsonl_records
# helpers) RETIRED 2026-07-19 with the live-kernel retirement of Skill_Promotions and
# Loop_Breaker_Fires (metric-surface review Part E item 3): their JSONL/state sources
# are never written on this host, so both metrics were permanently dark — removal,
# never faking. Their REGISTRY rows are gone too (the kernel-drift orphan guard
# forbids frozen rows absent from the live kernel).


def _governance_pass_rate(records: list[dict], repo_root: Path) -> float:  # noqa: ARG001
    """Fraction of verifier checks (root-hygiene + path-authority + runtime-contract) passing.

    Aggregates all three governance verifiers into a single 0-1 pass rate.
    Complements Root_Hygiene_Issues and Hardcoded_Path_Incidents which count failures;
    this provides the overall health percentage.
    """
    try:
        from execution.verify_root_hygiene import run_checks as rh
        from execution.verify_path_authority import run_checks as pa
        from execution.verify_runtime_contract import run_checks as rc
        all_results = rh(repo_root=repo_root) + pa(repo_root=repo_root) + rc(repo_root=repo_root)
        if not all_results:
            return 0.0
        passes = sum(1 for r in all_results if r.get("status") in ("OK", "PASS"))
        return round(passes / len(all_results), 3)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------
# Shape: {pillar, metric, source, reducer, tier}
# reducer signature: (records: list[dict], repo_root: Path) -> float | int | str

REGISTRY: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Brush — Local_Routing_Share  (already LIVE)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Local_Routing_Share",
        "source": "telemetry.model_tier",
        "reducer": _local_routing_share,
        "tier": "DERIVED",
    },
    # ------------------------------------------------------------------
    # Brush — Root_Hygiene_Issues  (already LIVE — reads from verifier)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Root_Hygiene_Issues",
        "source": "verifier.root_hygiene",
        "reducer": _count_root_hygiene_fails,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Brush — Hardcoded_Path_Incidents  (already LIVE — reads from verifier)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Hardcoded_Path_Incidents",
        "source": "verifier.path_authority",
        "reducer": _count_hardcoded_path_fails,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Skill_Promotions row RETIRED 2026-07-19 with the live-kernel metric
    # (dead source — see the reducer retirement note above).
    # ------------------------------------------------------------------
    # Bow — Governance_Pass_Rate
    # Combined pass rate across all three governance verifiers (0-1).
    # Complements Root_Hygiene_Issues and Hardcoded_Path_Incidents.
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Governance_Pass_Rate",
        "source": "verifier.root_hygiene+path_authority+runtime_contract",
        "reducer": _governance_pass_rate,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # Loop_Breaker_Fires row RETIRED 2026-07-19 with the live-kernel metric
    # (dead source — see the reducer retirement note above).
    # ------------------------------------------------------------------
    # SWORD — Kill_Chains_Disrupted  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Kill_Chains_Disrupted",
        "key": "Kill_Chains_Disrupted",
        "source": "state/kill_chain_events.jsonl",
        "reducer": _kill_chains_disrupted,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # BOW — Estimated_Agent_Time_Saved  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "bow",
        "metric": "Estimated_Agent_Time_Saved",
        "key": "Estimated_Agent_Time_Saved",
        "source": "state/MEDITATION_STATE.json|telemetry.records",
        "reducer": _estimated_agent_time_saved,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # BRUSH — Estimated_Cost_Savings  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "brush",
        "metric": "Estimated_Cost_Savings",
        "key": "Estimated_Cost_Savings",
        "source": "state/budget_ledger.json|telemetry.records",
        "reducer": _estimated_cost_savings,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # ARTS — Estimated_Human_Time_Saved  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "arts",
        "metric": "Estimated_Human_Time_Saved",
        "key": "Estimated_Human_Time_Saved",
        # Declared source must match what the reducer actually reads (live-source-scan
        # resolves these tokens literally): _vibe_alignment_score reads
        # state/vibe_alignment.json; doc-parity latency is computed from
        # state/charters/*.md mtimes — no doc_parity.json is ever read. The weekly
        # vibe/doc gains also read Data/telemetry/metrics_history.jsonl (repo-root
        # relative via ../..) — without it the metric silently drops those hour
        # components while staying LIVE, exactly what this scan exists to catch.
        # NOT declared: state/calibration_coefficients.json (missing -> reducer
        # errors -> SIMULATED, the safe direction) and ~/.claude/data/
        # skill_promotion_log.jsonl (known-dead source, gap carried honestly as
        # "0h promos"; declaring it would flip doctor to FAIL on a documented gap).
        "source": "state/MEDITATION_STATE.json+state/vibe_alignment.json+state/charters/*.md+../../Data/telemetry/metrics_history.jsonl",
        "reducer": _estimated_human_time_saved,
        "tier": "AUTO",
    },
    # ------------------------------------------------------------------
    # SWORD — Pending_Chain_Proposals  (NEW)
    # ------------------------------------------------------------------
    {
        "pillar": "sword",
        "metric": "Pending_Chain_Proposals",
        "key": "Pending_Chain_Proposals",
        "source": "state/proposed_kill_chains.json",
        "reducer": _pending_chain_proposals,
        "tier": "AUTO",
    },
]

for _r in REGISTRY:
    _r["key"] = _r.get("key", _r["metric"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_metric(
    name: str,
    records: list[dict],
    repo_root: Path,
) -> dict[str, Any]:
    """Compute a single metric by name and return a result envelope."""
    entry = next((e for e in REGISTRY if e["metric"] == name), None)
    if entry is None:
        return {
            "metric": name,
            "value": None,
            "source": "unknown",
            "tier": "unknown",
            "live": False,
            "error": f"metric '{name}' not found in REGISTRY",
        }
    try:
        value = entry["reducer"](records, repo_root)
    except Exception as exc:
        return {
            "metric": name,
            "value": None,
            "source": entry["source"],
            "tier": entry["tier"],
            "live": False,
            "error": str(exc),
        }
    
    calibrated = True
    if isinstance(value, dict):
        calibrated = value.get("calibrated", True)
        if value.get("error"):
            return {
                "metric": name,
                "value": None,
                "source": entry["source"],
                "tier": entry["tier"],
                "live": False,
                "error": value["error"],
            }
        value = value.get("val")

    return {
        "metric": name,
        "value": value,
        "source": entry["source"],
        "tier": entry["tier"],
        "live": True,
        "calibrated": calibrated,
    }


def main() -> int:
    records = load_telemetry_records(_REPO_ROOT)
    errors = []
    for entry in REGISTRY:
        result = compute_metric(entry["metric"], records, _REPO_ROOT)
        if not result.get("live"):
            errors.append(result)
            print(f"[FAIL] {entry['metric']}: {result.get('error', 'unknown')}")
        else:
            print(f"[OK] {entry['metric']}: {result['value']}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
