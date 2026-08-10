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
_samurai_data = Path(os.environ.get("SAMURAI_HOME", Path.home() / ".samurai")) / "data"
_RUNTIME_DATA = _samurai_data if _samurai_data.is_dir() else Path.home() / ".claude" / "data"


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


def _check_prompt_injection(days: int = 7) -> bool:
    # High-confidence unmatched prompt injection events (confidence >= 0.5)
    unmatched = _recent_events(_OS_ROOT / "state" / "kill_chain_unmatched.jsonl", days=days)
    return any((e.get("confidence") or 0.0) >= 0.5 for e in unmatched
               if e.get("event_type") == "prompt_injection")


# --- per-gate block readers (2026-07-12) ---
# The four PreToolUse gates used to share one reader over security_gate_log.jsonl,
# a file NOTHING writes on this host: every gate silently reported False (33 real
# shell-gate blocks/7d invisible), and had the file existed, one gate's finding
# would have fired all four (inflating taxonomy-chain confidence). The canonical
# per-gate source is hook_timings.jsonl, whose `hook` field names the gate.

_HOOK_TIMINGS_NAME = "hook_timings.jsonl"
# Single-entry cache keyed by (resolved path, mtime_ns, size, days): the file is
# ~22 MB and four gate checks per run() would otherwise parse it four times. Not
# @lru_cache — other processes append this file continuously (Anti-Pattern #6).
# The path is IN the key: without it, two different _RUNTIME_DATA roots whose
# files happen to share (mtime_ns, size) would collide and serve stale data
# (the exact leak the test suite triggers by patching _RUNTIME_DATA per test).
_gate_blocks_cache: dict[tuple, set] = {}


def _is_block_row(o: dict) -> bool:
    """A deliberate gate block — NOT a crash. status=='error' with a nonzero exit
    is a hook that threw (507 such rows exist for unrelated hooks), and must not
    be miscounted as a security decision. A block is an explicit 'blocked' status,
    or a bare nonzero exit with no status field (older rows predate the status
    key). 'ok'/'error'/'timeout' statuses never count."""
    status = o.get("status")
    if status == "blocked":
        return True
    if status is None:
        return o.get("exit_code") not in (0, None)
    return False


def _blocked_hook_names(days: int = 7) -> set:
    path = _RUNTIME_DATA / _HOOK_TIMINGS_NAME
    try:
        st = path.stat()
    except OSError:
        return set()
    key = (str(path), st.st_mtime_ns, st.st_size, days)
    if key not in _gate_blocks_cache:
        _gate_blocks_cache.clear()
        _gate_blocks_cache[key] = {
            o.get("hook") for o in _recent_events(path, days=days) if _is_block_row(o)
        }
    return _gate_blocks_cache[key]


def _make_gate_check(hook_name: str):
    """A zero-arg-callable reader: did THIS gate block anything in the window?"""
    def check(days: int = 7) -> bool:
        return hook_name in _blocked_hook_names(days)
    return check


# Map each taxonomy detection_point to a check function.
# Note: security-gate is a non-blocking commit nudge (never exits 2), so its
# reader honestly reports False. Chains 1/2/6 keep it as a KNOWN-UNIMPLEMENTED
# detection point: it caps their confidence at partial coverage, which is the
# honest signal ("we don't yet have a blocking control for this technique").
# A 2026-07-12 remap onto `guardrails` was REVERTED — guardrails only matches
# local dev-command safety (rm -rf, force-push, .ssh reads); it has no
# cron/launchctl or phishing patterns, so firing it for those chains would
# manufacture false-positive confidence (adversarial-verify finding). Building
# a real spearphishing/cron detector, or redesigning security_gate into a
# blocking gate, stays HUMAN-LANE.
_SIGNAL_CHECKS: dict[str, object] = {
    "secret_scrubber":        _check_secret_scrubber,
    "secret_scrubber_realtime": _check_scrubber_realtime,
    "security_gate":          _make_gate_check("security-gate"),
    "protected_shell_gate":   _make_gate_check("protected-shell-gate"),
    "protected_asset_gate":   _make_gate_check("protected-asset-gate"),
    "python_script_gate":     _make_gate_check("python-script-gate"),
    "prompt_injection_guard": _check_prompt_injection,
}


def run(runtime_root: Path | None = None) -> dict:  # noqa: ARG001
    """Discover untracked kill chains and write proposals. Returns signal counts."""
    tax = _read_json(_OS_ROOT / "state" / "kill_chain_taxonomy.json")
    if not isinstance(tax, dict):
        # None, not 0. "I checked and found no untracked chains" and "I could not read the
        # taxonomy" are opposite facts, and 0 is the healthiest value this signal has — so
        # collapsing them makes a missing source look like a clean bill of health. The
        # caller omits a None rather than recording it.
        return {
            "kill_chain_candidates": None,
            "chains_checked": None,
            "data_gap": "state/kill_chain_taxonomy.json missing or unreadable",
        }

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
