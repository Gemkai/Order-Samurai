"""experiments — the file/verdict/escalate registry for the weekly experiment lane.

Turns the one real A/B on record (graph_query vs. filesystem crawl, prose in CLAUDE.md)
into a repeatable process: file a hypothesis (frozen at file time — this registry has no
draft state, matching ab-test-setup's "commit before launch" discipline), get a verdict
recorded later, and escalate to human review when the result can't stand on its own.

Deliberately a SEPARATE file/schema from SENSEI_LEDGER.jsonl (see Phase 1's
ledger_efficacy.py for the same discipline) — `hypothesis/primary_metric/guardrails/arm/
sample_size` has nothing to do with `reflex_id/pillar/scout_verdict/rival_verdict`.

Append-only, same idiom as every other ledger in this codebase: a verdict is recorded as
a NEW row carrying the same `experiment_id`, never a rewrite of the filed row. Two row
`kind`s share one file:
  - "filed"   — {ts, experiment_id, kind, hypothesis, primary_metric, guardrails, arm,
                 sample_size, verdict: null, frozen_at, filed_by}
  - "verdict" — {ts, experiment_id, kind, verdict, evidence, guardrail_violated}
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_STATE_DIR = Path(__file__).resolve().parents[1] / "Order Samurai" / "state"
_EXPERIMENTS = _STATE_DIR / "EXPERIMENTS.jsonl"
_BACKLOG = _STATE_DIR / "PROPOSED_BACKLOG.json"

# context-ablation's own 5-way taxonomy (SKILL.md "Verdicts" table) — the only vocabulary
# a verdict row may carry. Not extended here; a new verdict kind is a context-ablation
# change, not an experiments.py one.
VERDICTS = ("LOAD-BEARING", "REDUNDANT", "DEAD", "INVERTED", "INCONCLUSIVE")

_EXP_ID_RE = re.compile(r"^EXP-(\d+)$")

# Default pillar for an escalated PROPOSED_BACKLOG.json entry when the caller doesn't name
# one. Simplification, stated explicitly rather than guessed: unlike a governance metric
# reflex, an experiment's primary_metric isn't reliably mappable to a pillar (it may not
# be a METRIC_CONFIG entry at all — "does this skill's prose matter" isn't a pillar
# metric). Callers that know the right pillar should pass it; this is the honest fallback,
# matching the roadmap's own stated default.
_DEFAULT_PILLAR = "brush"


def experiments_path() -> Path:
    return _EXPERIMENTS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iter_rows(path: Optional[Path] = None) -> list[dict]:
    p = path or _EXPERIMENTS
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append_row(row: dict, path: Optional[Path] = None) -> dict:
    record = dict(row)
    record.setdefault("ts", _now_iso())
    p = path or _EXPERIMENTS
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _next_experiment_id(path: Optional[Path] = None) -> str:
    """EXP-<n>, one past the highest existing id. Scan-based, same assumption
    harness_lineage's round numbering makes (single-writer weekly cadence, not
    high-concurrency) — the mechanism this registry serves fires at most weekly."""
    highest = 0
    for row in _iter_rows(path):
        m = _EXP_ID_RE.match(str(row.get("experiment_id", "")))
        if m:
            highest = max(highest, int(m.group(1)))
    return f"EXP-{highest + 1}"


def file_experiment(
    hypothesis: str,
    primary_metric: str,
    guardrails: list[str],
    arm: str,
    sample_size: int,
    filed_by: str,
    path: Optional[Path] = None,
) -> str:
    """File a frozen experiment. Returns its experiment_id.

    Filing IS freezing — there is no separate unfrozen-draft state (matches
    ab-test-setup's hard gate: hypothesis, primary metric, and sample size must already
    be locked before this is called; that discipline happens upstream of this function,
    not inside it).
    """
    if not hypothesis.strip():
        raise ValueError("hypothesis must not be empty — nothing to test")
    if not primary_metric.strip():
        raise ValueError("primary_metric must not be empty — nothing to grade")
    experiment_id = _next_experiment_id(path)
    ts = _now_iso()
    _append_row(
        {
            "ts": ts,
            "experiment_id": experiment_id,
            "kind": "filed",
            "hypothesis": hypothesis,
            "primary_metric": primary_metric,
            "guardrails": guardrails,
            "arm": arm,
            "sample_size": sample_size,
            "verdict": None,
            "frozen_at": ts,
            "filed_by": filed_by,
        },
        path,
    )
    return experiment_id


def next_pending(path: Optional[Path] = None) -> Optional[dict]:
    """Oldest filed row with no later verdict row for its experiment_id, or None."""
    rows = _iter_rows(path)
    filed_by_id = {r["experiment_id"]: r for r in rows if r.get("kind") == "filed"}
    verdicted_ids = {r["experiment_id"] for r in rows if r.get("kind") == "verdict"}
    pending = [r for eid, r in filed_by_id.items() if eid not in verdicted_ids]
    if not pending:
        return None
    return min(pending, key=lambda r: r.get("ts", ""))


def record_verdict(
    experiment_id: str,
    verdict: str,
    evidence: str,
    guardrail_violated: bool = False,
    path: Optional[Path] = None,
    backlog_path: Optional[Path] = None,
) -> bool:
    """Append a verdict row for `experiment_id`. Returns False (writes nothing) when no
    OPEN filed row matches — either the id is unknown, or it already has a verdict."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict {verdict!r} not one of {VERDICTS}")
    rows = _iter_rows(path)
    filed = next((r for r in rows if r.get("kind") == "filed" and r.get("experiment_id") == experiment_id), None)
    if filed is None:
        return False
    already_verdicted = any(
        r.get("kind") == "verdict" and r.get("experiment_id") == experiment_id for r in rows
    )
    if already_verdicted:
        return False
    _append_row(
        {
            "ts": _now_iso(),
            "experiment_id": experiment_id,
            "kind": "verdict",
            "verdict": verdict,
            "evidence": evidence,
            "guardrail_violated": guardrail_violated,
        },
        path,
    )
    _escalate_if_needed(filed, verdict, evidence, guardrail_violated, path, backlog_path)
    return True


def _inconclusive_streak_for_hypothesis(hypothesis: str, up_to_ts: str, path: Optional[Path]) -> int:
    """Consecutive INCONCLUSIVE verdicts (newest-first) among filed experiments sharing
    the exact same hypothesis text, ending at `up_to_ts`. A "retry" of an inconclusive
    experiment is defined as a new filing with identical hypothesis wording — simple and
    deterministic; anything more elaborate (fuzzy matching) is out of scope here."""
    rows = _iter_rows(path)
    filed_ids = [
        r["experiment_id"] for r in rows
        if r.get("kind") == "filed" and r.get("hypothesis") == hypothesis and r.get("ts", "") <= up_to_ts
    ]
    verdicts_by_id = {
        r["experiment_id"]: r for r in rows if r.get("kind") == "verdict"
    }
    # Order filed ids by their own ts, newest first.
    ordered = sorted(
        (r for r in rows if r.get("kind") == "filed" and r.get("experiment_id") in filed_ids),
        key=lambda r: r.get("ts", ""),
        reverse=True,
    )
    streak = 0
    for r in ordered:
        v = verdicts_by_id.get(r["experiment_id"])
        if v is None or v.get("verdict") != "INCONCLUSIVE":
            break
        streak += 1
    return streak


def _backlog_has_id(backlog: dict, item_id: str) -> bool:
    return any(item.get("id") == item_id for item in backlog.get("items", []))


def _escalate_if_needed(
    filed_row: dict,
    verdict: str,
    evidence: str,
    guardrail_violated: bool,
    path: Optional[Path],
    backlog_path: Optional[Path] = None,
) -> None:
    """INCONCLUSIVE twice on the same hypothesis, or a guardrail violation, gets a
    PROPOSED_BACKLOG.json entry — same shape as the real IMPSYS-<n> entries (confirmed
    against the live file). Idempotent: checks for an existing entry with this
    experiment_id before appending, so a re-scan never duplicates it."""
    experiment_id = filed_row["experiment_id"]
    needs_escalation = guardrail_violated or (
        verdict == "INCONCLUSIVE"
        and _inconclusive_streak_for_hypothesis(filed_row["hypothesis"], filed_row["ts"], path) >= 2
    )
    if not needs_escalation:
        return

    backlog_path = backlog_path or _BACKLOG
    try:
        backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        backlog = {"items": [], "generated_at": _now_iso()}
    if _backlog_has_id(backlog, experiment_id):
        return

    reason = "guardrail violated" if guardrail_violated else "INCONCLUSIVE twice on the same hypothesis"
    title = filed_row["hypothesis"]
    if len(title) > 80:
        title = title[:77] + "..."
    backlog.setdefault("items", []).append(
        {
            "id": experiment_id,
            "kind": "experiment",
            "pillar": _DEFAULT_PILLAR,
            "title": title,
            "triage_note": f"{reason} | verdict={verdict} | evidence={evidence[:200]}",
            "status": "proposed",
            "approved": False,
        }
    )
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_path.write_text(json.dumps(backlog, indent=2), encoding="utf-8")
