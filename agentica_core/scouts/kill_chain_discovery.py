"""Kill chain discovery scout — correlates live telemetry signals against the
MITRE ATT&CK taxonomy (kill_chain_taxonomy.json) and proposes untracked chains.

Writes proposed_kill_chains.json under the Order Samurai state directory.
Returns:
    kill_chain_candidates: int   — new proposals written this run
    chains_checked: int          — taxonomy chains examined
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

_OS_ROOT = Path(os.environ.get("ORDER_SAMURAI_ROOT",
    str(Path(__file__).resolve().parents[2] / "Order Samurai")))
_RUNTIME_DATA = Path.home() / ".claude" / "data"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _recent_events(path: Path, days: int = 7) -> list[dict]:
    """Non-comment JSON lines from *path* whose ts/timestamp falls within the last *days* days."""
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        try:
            obj = json.loads(ln)
            ts_raw = obj.get("ts") or obj.get("timestamp")
            if ts_raw:
                dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    out.append(obj)
        except Exception:
            continue
    return out


def _chain_ids_in_events(days: int = 30) -> set:
    """Chain IDs that already appear in kill_chain_events.jsonl within the last *days* days."""
    events = _recent_events(_OS_ROOT / "state" / "kill_chain_events.jsonl", days=days)
    return {e["chain_id"] for e in events if e.get("chain_id") is not None}


# --- per-detection-point signal checks ---

def _check_secret_scrubber(days: int = 7) -> bool:
    for obj in _recent_events(_RUNTIME_DATA / "secret_scrubber.jsonl", days=days):
        if int(obj.get("findings_count") or 0) > 0:
            return True
    return False


def _check_scrubber_realtime(days: int = 7) -> bool:
    return bool(_recent_events(_RUNTIME_DATA / "secret_scrubber_realtime.jsonl", days=days))


def _check_security_gate(days: int = 7) -> bool:
    for obj in _recent_events(_RUNTIME_DATA / "security_gate_log.jsonl", days=days):
        if obj.get("findings") or obj.get("finding_count") or obj.get("exit_code"):
            return True
    return False


def _check_prompt_injection(days: int = 7) -> bool:
    # High-confidence unmatched prompt injection events (confidence >= 0.5)
    unmatched = _recent_events(_OS_ROOT / "state" / "kill_chain_unmatched.jsonl", days=days)
    return any((e.get("confidence") or 0.0) >= 0.5 for e in unmatched
               if e.get("event_type") == "prompt_injection")


# Map each taxonomy detection_point to a check function
_SIGNAL_CHECKS: dict[str, object] = {
    "secret_scrubber":        _check_secret_scrubber,
    "secret_scrubber_realtime": _check_scrubber_realtime,
    "security_gate":          _check_security_gate,
    "protected_shell_gate":   _check_security_gate,
    "protected_asset_gate":   _check_security_gate,
    "python_script_gate":     _check_security_gate,
    "prompt_injection_guard": _check_prompt_injection,
}


def run(runtime_root: Path | None = None) -> dict:  # noqa: ARG001
    """Discover untracked kill chains and write proposals. Returns signal counts."""
    tax = _read_json(_OS_ROOT / "state" / "kill_chain_taxonomy.json")
    if not isinstance(tax, dict):
        return {"kill_chain_candidates": 0, "chains_checked": 0}

    chains = tax.get("chains", [])
    already_tracked = _chain_ids_in_events(days=30)

    # Cache each detection-point check — each reads a file; only run once
    sig_cache: dict[str, bool] = {}

    def _has_signal(dp: str) -> bool:
        if dp not in sig_cache:
            fn = _SIGNAL_CHECKS.get(dp)
            sig_cache[dp] = fn() if fn else False
        return sig_cache[dp]

    now_iso = datetime.now(timezone.utc).isoformat()
    proposals: list[dict] = []
    for chain in chains:
        cid = chain.get("id")
        if cid in already_tracked:
            continue
        dps = chain.get("detection_points") or []
        if not dps:
            continue
        firing = [dp for dp in dps if _has_signal(dp)]
        if not firing:
            continue
        proposals.append({
            "chain_id": cid,
            "name": chain.get("name", ""),
            "status": "proposed",
            "proposed_at": now_iso,
            "firing_detection_points": firing,
            "all_detection_points": dps,
            "confidence": round(len(firing) / len(dps), 2),
        })

    # Merge with existing file — keep approved/rejected entries; replace proposed ones
    proposals_path = _OS_ROOT / "state" / "proposed_kill_chains.json"
    existing = _read_json(proposals_path) or {"proposals": [], "last_run": None, "approved_count": 0}
    kept = [p for p in (existing.get("proposals") or []) if p.get("status") != "proposed"]
    existing["proposals"] = kept + proposals
    existing["last_run"] = now_iso

    try:
        proposals_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError:
        pass

    return {"kill_chain_candidates": len(proposals), "chains_checked": len(chains)}
