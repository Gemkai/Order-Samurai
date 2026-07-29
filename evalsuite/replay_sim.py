"""replay_sim — deterministic stand-in for the reflex-engine loop-breaker/cooldown contract.

Group D's tasks need to answer: under THESE knob values, how does the autonomy channel behave
against a known outcome pattern — does a permanent failure get parked cheaply, does a transient
failure still get its recovery attempt, does a quota window survive, does a re-degrading metric
thrash the budget? The real engine is TypeScript wired to spawns and wall clocks; this is the
same CONTRACT in ~60 lines of pure Python so a candidate knob set can be evaluated in
microseconds.

Contract mirrored from `api/src/reflex-engine.ts` (keep in sync BY HAND — if the TS semantics
change, change this file and its tests in the same commit and say so):
  - hard bucket: a run that errored OR completed without improving -> consecutive += 1;
    parked (stuck) when consecutive >= loop_breaker_limit.
  - incomplete bucket: timeout / turn-cap / quota -> incompleteConsecutive += 1; parked when
    incompleteConsecutive >= incomplete_limit. "Didn't finish" is weaker evidence than "failed",
    hence the separate, more lenient budget.
  - improvement resets BOTH counters.
  - cooldown (reflex_cooldown_minutes) is armed at every run completion; the reflex cannot
    re-fire before it expires. Parked is permanent within a simulation.

Knob values are read through `harness_config.get_value`, so an `OS_HARNESS_<KEY>` env override —
the mechanism the self-harness cycle uses to pin a candidate — flows straight through. This
module is the consumer-under-test; the graders that call it assert LITERAL expectations only.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_GOV_ROOT = Path(__file__).resolve().parents[2]
if str(_GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOV_ROOT))

from agentica_core import harness_config  # noqa: E402

# Outcome vocabulary -> bucket. 'improved' is the only counter-reset.
_HARD = ("error", "no_change")
_INCOMPLETE = ("timeout", "quota")
OUTCOMES = _HARD + _INCOMPLETE + ("improved",)


@dataclass
class ReplayResult:
    spawns: int              # how many runs the channel actually spent
    parked: bool             # did the loop-breaker park the reflex
    parked_after: int | None # spawn count at park time
    recovered: bool          # did an 'improved' run ever execute


def simulate(
    outcomes: list[str],
    *,
    duration_minutes: int,
    re_degrade: bool = False,
) -> ReplayResult:
    """Replay a reflex that wants to fire whenever eligible, for `duration_minutes`.

    `outcomes[i]` is what the i-th spawn does; when the list is exhausted the last outcome
    repeats (a permanent condition). `re_degrade=True` means the metric goes bad again right
    after every improvement — the thrash scenario — otherwise an improvement ends the episode.
    """
    for o in outcomes:
        if o not in OUTCOMES:
            raise ValueError(f"unknown outcome {o!r}; expected one of {OUTCOMES}")
    if not outcomes:
        raise ValueError("outcomes must not be empty")

    loop_breaker_limit = int(harness_config.get_value("loop_breaker_limit"))
    incomplete_limit = int(harness_config.get_value("incomplete_limit"))
    cooldown_minutes = int(harness_config.get_value("reflex_cooldown_minutes"))

    consecutive = 0
    incomplete_consecutive = 0
    spawns = 0
    recovered = False

    t = 0
    while t <= duration_minutes:
        outcome = outcomes[spawns] if spawns < len(outcomes) else outcomes[-1]
        spawns += 1

        if outcome == "improved":
            recovered = True
            consecutive = 0
            incomplete_consecutive = 0
            if not re_degrade:
                return ReplayResult(spawns, False, None, True)
        elif outcome in _HARD:
            consecutive += 1
            if consecutive >= loop_breaker_limit:
                return ReplayResult(spawns, True, spawns, recovered)
        else:  # incomplete
            incomplete_consecutive += 1
            if incomplete_consecutive >= incomplete_limit:
                return ReplayResult(spawns, True, spawns, recovered)

        # Cooldown armed at completion; the next eligible fire is one cooldown later.
        t += cooldown_minutes

    return ReplayResult(spawns, False, None, recovered)
