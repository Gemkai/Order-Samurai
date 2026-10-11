"""Operational counters read from event logs and ledgers: kill-chain counts and pending proposals, compaction and daemon-restart events, and repository-hygiene readers for stale branches, lane age and unexecuted approvals (split from aggregate.py)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from .aggregate_records import _SCOUT_CACHE_TTL_SEC, _file_signature, _target_week_anchor


# Remediation actions that count as actually disrupting a chain — the single
# source of truth for "disrupted" vs "still open" across the kill-chain reducers.
_DISRUPT_ACTIONS = ("block", "patch", "remediat", "quarantine", "revert")


def _kill_chain_week_sets(paths: list[Path], records: list[dict] | None = None) -> tuple[set, set, set, set]:
    """Distinct chain_ids with events this/last ISO week across all paths, split into
    (this_detected, last_detected, this_disrupted, last_disrupted).
    Disrupted = at least one event whose remediation_action goes beyond logging.
    Uses sets so the same chain_id from multiple files is counted only once.

    `records` anchors "this week" via _target_week_anchor (2026-08-08 class sweep,
    missed here originally): backfill_history.py replays one historical ISO week of
    telemetry at a time, and a wall-clock `now` stamped every replayed week with
    whatever the CURRENT real week's kill-chain count happened to be."""
    from . import aggregate as _agg
    now = _target_week_anchor(records if records is not None else [])
    this_week = now.strftime("%G-W%V")
    last_week = (now - timedelta(days=7)).strftime("%G-W%V")
    signature = (this_week, last_week, _file_signature(paths))
    now_mono = time.monotonic()
    if _agg._KILL_CHAIN_WEEK_MEMO is not None:
        cached_at, cached_signature, cached = _agg._KILL_CHAIN_WEEK_MEMO
        if now_mono - cached_at < _agg._KILL_CHAIN_WEEK_TTL_S and signature == cached_signature:
            return tuple(set(items) for items in cached)

    this_det: set = set()
    last_det: set = set()
    this_dis: set = set()
    last_dis: set = set()
    read_failed = False
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            read_failed = True
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts = obj.get("ts")
                chain_id = obj.get("chain_id")
                if ts is None or chain_id is None:
                    continue
                wk = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%G-W%V")
                action = str(obj.get("remediation_action", "")).lower()
                disruptive = any(a in action for a in _DISRUPT_ACTIONS)
                if wk == this_week:
                    this_det.add(chain_id)
                    if disruptive:
                        this_dis.add(chain_id)
                elif wk == last_week:
                    last_det.add(chain_id)
                    if disruptive:
                        last_dis.add(chain_id)
            except Exception:
                continue
    result = (this_det, last_det, this_dis, last_dis)
    frozen = tuple(frozenset(items) for items in result)
    if not read_failed:
        _agg._KILL_CHAIN_WEEK_MEMO = (now_mono, signature, frozen)
    return tuple(set(items) for items in frozen)


_KC_PRUNE = {"node_modules", ".git", "__pycache__", "dist", ".venv", "venv",
             "sub-bundles", ".tmp", "artifacts", ".ruff_cache", ".pytest_cache"}


def _kill_chains_disrupted(records: list[dict], repo_root: Path | None = None) -> dict:
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    paths = _agg._kill_chain_paths(repo_root)
    if not any(p.exists() for p in paths):
        # No kill_chain_events source anywhere = the emitter is dead/unwired. A bare
        # "0 disrupted" here is indistinguishable from a genuinely secure week, so flag
        # a data_gap and let the hero fall back to a real measured security signal
        # (Secrets_Detected) instead of presenting a confident — possibly false — 0.
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    try:
        _, _, this_dis, last_dis = _kill_chain_week_sets(paths, records)
        return {"val": len(this_dis), "week_delta": len(this_dis) - len(last_dis), "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _kill_chains_open(records: list[dict], repo_root: Path | None = None) -> dict:
    """Open exposure: chains detected this ISO week with NO disruptive event yet
    (block/patch/quarantine/revert). Graded successor of the ungraded
    Kill_Chains_Detected row (2026-07-08 audit consolidation) — the actionable
    number is the detected−disrupted gap, not raw detections."""
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    paths = _agg._kill_chain_paths(repo_root)
    if not any(p.exists() for p in paths):
        # same honesty rule as _kill_chains_disrupted: a bare 0 with no emitter is
        # indistinguishable from a secure week — flag the gap
        return {"val": 0, "week_delta": 0, "calibrated": True, "data_gap": True}
    try:
        this_det, last_det, this_dis, last_dis = _kill_chain_week_sets(paths, records)
        this_open = len(this_det - this_dis)
        last_open = len(last_det - last_dis)
        return {"val": this_open, "week_delta": this_open - last_open, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _pending_chain_proposals(records: list[dict], repo_root: Path | None = None) -> dict:  # noqa: ARG001
    from . import aggregate as _agg
    if repo_root is None:
        repo_root = _agg._ORDER_SAMURAI_ROOT
    path = repo_root / "state" / "proposed_kill_chains.json"
    if not path.exists():
        return {"val": 0, "week_delta": 0, "calibrated": True}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        proposals = data.get("proposals", [])
        val = sum(1 for p in proposals if p.get("status") == "proposed")
        return {"val": val, "week_delta": 0, "calibrated": True}
    except Exception as e:
        return {"val": None, "error": f"source unavailable: {str(e)}", "calibrated": False}


def _compaction_events(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-010 (Compaction Events): count of REAL context-compaction events, read
    directly from Claude Code session transcripts (~/.claude/projects/**/*.jsonl).

    Compaction has no telemetry emitter on this host — the `records` stream every
    other reducer here consumes never carries it — so, like _cache_hit_rate, this
    reducer ignores `records` and scans the transcripts directly, with a short TTL
    cache so repeated aggregate() calls in one refresh don't re-scan.

    UNLIKE _cache_hit_rate/_agent_spawn_events, this scans the FULL transcript
    corpus rather than a bounded recent-file window: compact_boundary is a RARE
    event (measured 2026-07-16: exactly 2 real events across 843 transcripts, at
    recency ranks 93 and 254 — a 40-60 file recent-window, the house convention for
    high-frequency signals like cache reads, would silently miss BOTH and always
    read 0, which is worse than a small honest count). A cheap substring
    pre-filter keeps the full scan fast, so the TTL cache exists only to dedupe
    repeat calls within one refresh, not to bound cost.

    The pre-filter runs over the WHOLE FILE AS BYTES, not per decoded line, and
    that distinction is essentially this reducer's entire cost. The corpus is
    append-only and unbounded: it grew from the 843 files / ~0.36s originally
    recorded here to 2,440 files / 1.4 GB / 4.76s by 2026-08-16 — a 10x regression
    with no code change, purely data growth. Decoding 1.4 GB of UTF-8 into lines to
    find a string that occurs 5 times is the expensive part; `needle in blob` is one
    C-level scan that skips almost every file whole, and only the few that match get
    split and decoded. Measured on this host 2026-08-16: 4.76s -> 0.65s, identical
    count. Keep the byte-level pre-filter if you touch this.

    A compaction event is a STRUCTURAL record: {"type": "system", "subtype":
    "compact_boundary", ...}. This must NOT be a substring/prose match — a naive
    regex for "compact" hits hundreds of unrelated lines where sessions merely
    DISCUSS compaction (this very metric's own name, quoted back in past
    governance transcripts). Only the structural type+subtype pair is counted.

    Returns a data_gap envelope (never a fabricated 0) only when the projects
    directory itself is missing/unreadable. A genuine full-corpus count of 0 (or
    2, or any small number) is an honest real measurement, not a data gap."""
    from . import aggregate as _agg
    now = time.monotonic()
    if (_agg._COMPACTION_CACHE["v"] is not None
            and now - _agg._COMPACTION_CACHE["t"] < _SCOUT_CACHE_TTL_SEC):
        return _agg._COMPACTION_CACHE["v"]
    projects_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".claude" / "projects"
    if not projects_dir.exists():
        result = {"val": None, "data_gap": True, "calibrated": True}
        _agg._COMPACTION_CACHE.update(t=now, v=result)
        return result
    jsonls = list(projects_dir.rglob("*.jsonl"))
    needle = b'"compact_boundary"'
    count = 0
    for jl in jsonls:
        try:
            with open(jl, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        if needle not in blob:
            # One C-level scan rejects the whole file. The vast majority of the
            # corpus exits here without ever being decoded or split.
            continue
        for raw in blob.split(b"\n"):
            if needle not in raw:
                continue
            try:
                entry = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if (isinstance(entry, dict) and entry.get("type") == "system"
                    and entry.get("subtype") == "compact_boundary"):
                count += 1
    result = {"val": count, "calibrated": True,
              "detail": f"{count} compact_boundary event(s) across {len(jsonls)} scanned transcripts"}
    _agg._COMPACTION_CACHE.update(t=now, v=result)
    return result


_DAEMON_LOG_LINE_RE = re.compile(r"^(?P<ts>\S+)\s+(?P<service>[\w.-]+):\s*(?P<message>.*)$")


def _daemon_restart_count(records: list[dict]) -> dict:  # noqa: ARG001
    """AUTO-012 (Daemon Restart Count): count of successful autonomic-daemon restarts,
    read from ~/.claude/scripts/service_supervisor.py's own log
    (~/.claude/data/service_supervisor.log). Prior cycles only scanned
    state/autonomic_events.jsonl, which the supervisor never writes to — this is the
    real, previously-unwired source (BLOCKER LIFTED 2026-07-16).

    Each line is `<ISO-ts> <service>: <message>`. A restart counts when the message
    is "restart successful"; lines that don't match the expected shape are skipped
    as malformed rather than crashing the reducer. Failed-spawn attempts ("spawn
    failed — ...") are real supporting signal surfaced in the detail string, but are
    NOT folded into the graded count — a successful restart and a failed spawn are
    different events.

    Returns a data_gap envelope (never a fabricated 0) when the log file doesn't
    exist yet. An existing log with zero restart lines is an honest 0, not a gap."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    log_path = home / ".claude" / "data" / "service_supervisor.log"
    if not log_path.exists():
        return {"val": None, "data_gap": True, "calibrated": True}
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"val": None, "error": f"source unavailable: {e}", "calibrated": False}

    restarts = 0
    failed_spawns = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _DAEMON_LOG_LINE_RE.match(line)
        if not m:
            continue  # malformed line — skipped, not counted either way
        message = m.group("message")
        if "restart successful" in message:
            restarts += 1
        elif "spawn failed" in message:
            failed_spawns += 1

    return {
        "val": restarts,
        "calibrated": True,
        "detail": f"{restarts} successful restarts, {failed_spawns} failed spawn attempts",
    }


# ---------------------------------------------------------------------------
# Git / factory hygiene trio — Stale_Branch_Count, Lane_Pending_Age,
# Approved_Unexecuted_HITL (bow/Autonomic, AUTO). Catalogued 2026-09-20 to surface
# unmerged branches, a stuck merge lane and unexecuted human approvals that none of
# the live metrics could see; intake approved 2026-09-21. The original proposal
# module re-exports these names, so its tests stay the contract.
#
# Inner contract: reducer(records, repo_root) -> int, repo_root = the Order Samurai
# root (the directory holding state/). `records` (telemetry) is ignored — each reads
# its own source. -1 ALWAYS means "source missing" and the r_* wrapper turns it into
# a SIMULATED envelope; it must never be emitted as a zero.
# ---------------------------------------------------------------------------
POLL_SECONDS = 900  # the dispatcher's poll; anything older than one poll is "stuck", not "in flight"


PROTECTED_BRANCHES = {"work", "main", "master", "autosave"}


PROTECTED_PREFIXES = ("backup/",)


PENDING_STATES = {"awaiting-lane", "pr-pending", "repair-pending"}


def _agentica_root(repo_root: Path) -> Path:
    """The AgenticaOS checkout that contains this Order Samurai root (two levels up)."""
    root = Path(repo_root).resolve()
    for cand in (root.parents[1] if len(root.parents) > 1 else root, root):
        if (cand / "Execution" / "factory").is_dir():
            return cand
    return root.parents[1] if len(root.parents) > 1 else root


def _ledger_path(repo_root: Path) -> Path:
    return _agentica_root(repo_root) / "Execution" / "factory" / "state" / "ledger.jsonl"


def _queue_path(repo_root: Path) -> Path:
    return Path(repo_root) / "state" / "hitl_queue.json"


def _now(now: _dt.datetime | None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(text: str) -> _dt.datetime | None:
    try:
        t = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def _git(root: Path, *args: str) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout.strip()


def _open_pr_branches_from_report(agentica_root: Path, now: _dt.datetime, max_age_days: int = 8) -> set[str]:
    """Branch names the latest branch_adjudicate dry-run kept because of an OPEN PR.

    gh is network + auth; the weekly report already paid that cost, so the reducer reuses it when
    it is fresh. No report (or a stale one) means no exclusion, which over-counts rather than hides.
    """
    reports = sorted((agentica_root / "Governance" / "data" / "worktree_sweep").glob("branch-adjudicate-*-dryrun.md"))
    if not reports:
        return set()
    latest = reports[-1]
    m = re.search(r"branch-adjudicate-(\d{4}-\d{2}-\d{2})", latest.name)
    if m:
        day = _parse_ts(m.group(1) + "T00:00:00+00:00")
        if day and (now - day).days > max_age_days:
            return set()
    names: set[str] = set()
    for line in latest.read_text(encoding="utf-8", errors="replace").splitlines():
        hit = re.match(r"^- (\S+) @[0-9a-f]+ — open PR", line)
        if hit:
            names.add(hit.group(1))
    return names


def _latest_ticket_states(ledger: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    with ledger.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:  # skip-tolerant: real ledgers carry the odd malformed line
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") == "ticket_state" and row.get("task_id"):
                latest[row["task_id"]] = row
    return latest


def _hygiene_envelope(n: int, missing: str) -> dict:
    """-1 (source missing) -> SIMULATED error envelope; anything else is a real count."""
    if n < 0:
        return {"val": None, "error": f"source unavailable: {missing}", "calibrated": False}
    return {"val": n, "calibrated": True}


_STALE_BRANCH_TTL_S = 300.0


def r_stale_branch_count(recs):  # noqa: ARG001
    """MEMOIZED with a short TTL, same reason as _unbounded_wait_count: the reducer
    spawns one `git merge-base` per local branch (measured 2026-09-23: 1.8s for ~150
    branches) and build_pillars() runs every reducer ~22 times per aggregate(). Refs
    cannot meaningfully change mid-refresh, so within-run reuse is exact; the TTL
    bounds cross-run staleness well under the 15-minute refresh cadence."""
    from . import aggregate as _agg
    now = time.monotonic()
    if _agg._STALE_BRANCH_CACHE["v"] is not None and now - _agg._STALE_BRANCH_CACHE["t"] < _STALE_BRANCH_TTL_S:
        return dict(_agg._STALE_BRANCH_CACHE["v"])
    result = _hygiene_envelope(_agg._stale_branch_count([], _agg._ORDER_SAMURAI_ROOT), "not a git checkout")
    _agg._STALE_BRANCH_CACHE.update(t=now, v=result)
    return dict(result)


def r_lane_pending_age(recs):  # noqa: ARG001
    from . import aggregate as _agg
    return _hygiene_envelope(_agg._lane_pending_age([], _agg._ORDER_SAMURAI_ROOT), "factory ledger missing")


def r_approved_unexecuted_hitl(recs):  # noqa: ARG001
    from . import aggregate as _agg
    return _hygiene_envelope(_agg._approved_unexecuted_hitl([], _agg._ORDER_SAMURAI_ROOT), "hitl_queue missing/malformed")
