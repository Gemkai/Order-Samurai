#!/usr/bin/env python3
"""tool_trust_annotator — what happened to a tool's result after the agent received it?

ATDP (arXiv 2607.01120) asks tool records to carry their downstream fate: whether the result was
"later trusted / ignored / corrected / contradicted". That field is the difference between knowing
a tool RAN and knowing it HELPED -- and unlike most quality signals, it is directly observable.

DELIBERATELY DETERMINISTIC -- no LLM, no judge, zero model calls. An identical retry is a FACT in
the transcript, not an opinion, and this stack's honesty ladder ranks a heuristic above an llm
judgment for exactly that reason. `Tool_Response_Utilization` already asks a 12B model to infer
utilisation from the next turn; this measures what actually happened instead.

Labels (all decided from transcript structure alone):
  trusted           -- no corrective action followed
  retried_identical -- the same tool ran again with the SAME arguments (the result was unusable).
                       The one unambiguously bad label here: re-asking an identical question is the
                       blind-retry mechanism the papers' harnesses had to forbid explicitly.
  errored_no_retry  -- the call errored and was not re-run verbatim. NOT a defect by default:
                       inspecting an error and adapting is exactly the behaviour a good harness
                       wants ("do not blindly retry the same action"). Named for what is observed,
                       not for a verdict -- an earlier draft called this `abandoned`, which would
                       have invited a reflex that punishes the agent for recovering correctly.

TWO of the paper's four labels are NOT implemented, and the reason is the same for both: they are
judgments wearing a heuristic's clothes.

  `corrected`    -- the obvious proxy ("same tool, different arguments") was BUILT, MEASURED, and
                    REMOVED. On 3,278 real tool calls it labelled 74% of them corrected, because
                    `Read a.py` then `Read b.py` is reading two files and five Bash commands in a
                    row is ordinary work -- neither is a correction. The proxy measured tool
                    REUSE, not correction, and would have driven the trust rate to a meaningless
                    23.6%. Detecting real correction needs to know whether two calls pursued the
                    same intent, which is judgment.
  `contradicted` -- deciding that a later fact contradicts an earlier result is judgment outright.

Their absence is an honest gap, declared in `not_implemented`. A fabricated proxy would have been
worse than a missing field: it would have looked like data.

Session boundaries are respected (transcript_source.iter_sessions): without them the last tool call
of one session and the first of the next look like a retry.

Usage:  python3 bin/tool_trust_annotator.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))

from agentica_core import harness_config  # noqa: E402
from agentica_core.atomic import atomic_json_write  # noqa: E402
from agentica_core.evals.transcript_source import Turn, iter_sessions  # noqa: E402

_STATE = Path(__file__).resolve().parents[1] / "state" / "tool_trust.json"

TRUST_LABELS = ("trusted", "retried_identical", "errored_no_retry")

# How many later tool calls count as "shortly after". A repeat 30 calls later is a new decision,
# not a retry of this one.
_LOOKAHEAD_CALLS = 5

# Substrings marking a tool_result as an error. Deliberately narrow: broad words like "failed"
# match results whose CONTENT merely discusses failure (a test report listing failures is not
# itself an error) -- the same false-positive class that bit the engine's quota regex.
_ERROR_MARKERS = ("<tool_use_error>", "error:", "traceback (most recent call last)",
                  "command not found", "no such file or directory", "permission denied")


def _is_error(result: Optional[str]) -> bool:
    if not result:
        return False
    low = result[:2000].lower()
    return any(m in low for m in _ERROR_MARKERS)


def _args_key(tool_input: dict) -> str:
    """Canonical argument signature; sorted so key order never fakes a difference."""
    try:
        return json.dumps(tool_input, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(sorted(tool_input.items())) if isinstance(tool_input, dict) else repr(tool_input)


def _flatten_calls(session: list[Turn]) -> list[dict]:
    """Session tool calls in order: {name, args_key, result, errored}."""
    out = []
    for turn in session:
        for tu in turn.tool_uses:
            out.append({
                "name": tu.name,
                "args_key": _args_key(tu.input if isinstance(tu.input, dict) else {}),
                "errored": _is_error(tu.result),
            })
    return out


def annotate_session(session: list[Turn]) -> list[dict]:
    """Label every tool call in one session by what the agent did next with its result."""
    calls = _flatten_calls(session)
    labelled = []
    for i, call in enumerate(calls):
        window = calls[i + 1: i + 1 + _LOOKAHEAD_CALLS]

        # Same tool AND identical arguments: the agent asked the exact same question twice, which
        # only happens when the first answer was unusable. Note this requires identical args --
        # "same tool, different args" is tool REUSE (reading a second file), not correction; see
        # the module docstring for why that proxy was removed after measurement.
        if any(c["name"] == call["name"] and c["args_key"] == call["args_key"] for c in window):
            label = "retried_identical"
        elif call["errored"]:
            # Errored and never re-run verbatim: usually the agent adapting, which is correct.
            label = "errored_no_retry"
        else:
            label = "trusted"

        labelled.append({"name": call["name"], "label": label, "errored": call["errored"]})
    return labelled


def _default_max_files() -> int:
    """Shares the declared scout scan bound -- one knob for "how much history a scout reads"."""
    try:
        return int(harness_config.get_value("scout_max_files"))
    except (OSError, ValueError, KeyError):
        return 60


def run_annotator(projects_dir: Optional[Path] = None, *, max_files: Optional[int] = None) -> dict:
    """Annotate recent sessions; return the payload written to state/tool_trust.json."""
    if max_files is None:
        max_files = _default_max_files()

    counts: Counter = Counter()
    per_tool: dict[str, Counter] = {}
    sessions = 0

    for session in iter_sessions(projects_dir, max_files=max_files):
        sessions += 1
        for rec in annotate_session(session):
            counts[rec["label"]] += 1
            per_tool.setdefault(rec["name"], Counter())[rec["label"]] += 1

    total = sum(counts.values())
    # A tool call is "trusted" when it neither errored nor was re-asked verbatim. None (not 0.0)
    # when nothing was observed: no data is a gap, never a perfect score.
    trust_rate = round(counts["trusted"] / total, 3) if total else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "~/.claude/projects/**/*.jsonl via evals.transcript_source.iter_sessions",
        "kind": "heuristic",  # deterministic: no model judged any of this
        "sessions_scanned": sessions,
        "tool_calls": total,
        "counts": {lbl: counts[lbl] for lbl in TRUST_LABELS},
        "trust_rate": trust_rate,
        "retry_rate": round(counts["retried_identical"] / total, 3) if total else None,
        "by_tool": {
            name: {lbl: c[lbl] for lbl in TRUST_LABELS if c[lbl]}
            for name, c in sorted(per_tool.items(), key=lambda kv: -sum(kv[1].values()))[:20]
        },
        # Declared, not silently absent: both need judgment. `corrected`'s deterministic proxy was
        # measured at a 74% false-positive rate on real data and removed. See the module docstring.
        "not_implemented": ["corrected", "contradicted"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Label tool results by their downstream fate.")
    ap.add_argument("--dry-run", action="store_true", help="print the payload; write nothing")
    args = ap.parse_args()

    payload = run_annotator()
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    atomic_json_write(_STATE, payload)
    print(
        f"wrote {_STATE}: {payload['tool_calls']} tool calls over {payload['sessions_scanned']} "
        f"sessions, trust_rate={payload['trust_rate']}, retry_rate={payload['retry_rate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
