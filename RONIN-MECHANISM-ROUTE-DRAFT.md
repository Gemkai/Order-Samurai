# Mechanism-route draft — wire `Deprecated_Deps` to the deterministic deps-audit

**Status: STAGED, NOT APPLIED.** Every edit below targets the **live Governance kernel**
(`C:\Users\jemak\Desktop\Agentica OS\Governance\...`), which is not in this repo. Apply in a
fresh, cautious session, showing each edit before writing (RONIN-DETERMINIZATION-PLAN.md
protocol). This file is the reviewable plan, not a change.

## What this wires

Route the reflex `metric:sword:Deprecated_Deps` to the deterministic mechanism
`bin/codebase_deps_audit.py` instead of the LLM skill `/codebase-cleanup-deps-audit`
(67% success, `state/skill_efficacy.json`), keeping the skill as the judgement-tail
fallback. Same pattern is ready to mirror onto `pip-safe-upgrade` (see §5).

The seams already exist — this is additive, not a rewrite:

- **`kind` field** — `aggregate.py:657` reads `item.get("kind", "skill")`; a mechanism run
  records `kind: "mechanism"`, which the Agent-Time-Saved reducer already benchmarks.
- **`mechanism_run` event** — already a canonical event type (`emit_event.py`,
  `telemetry.py` `AUTONOMIC_EVENTS`); `_estimated_cost_savings` comp2 counts
  `mechanism_run` events with `routing_efficient: true` (`aggregate.py:710`).
- **exec_log schema** — reflex runs already log
  `{command, skill, status, source:"reflex_engine", reflex_id, ...}` (`state/exec_log.jsonl`).

## Edit 1 — add a `mechanism` key to the METRIC_CONFIG entry (reflexes.py)

`METRIC_CONFIG` holds 43 entries keyed by metric, each with a `"skill"` key
(`.mex/ROUTER.md`). Locate the Sword `Deprecated_Deps` entry and add a sibling
`"mechanism"` block; leave `"skill"` untouched so it remains the fallback.

```python
# reflexes.py — METRIC_CONFIG, Sword pillar
"Deprecated_Deps": {
    # ...existing keys (threshold, pillar, etc.) unchanged...
    "skill": "codebase-cleanup-deps-audit",        # KEEP — judgement-tail fallback
    "mechanism": {                                  # ADD
        "cmd": ["python", "bin/codebase_deps_audit.py"],
        "produces": "~/.claude/data/dependency_audit.json",
        "read_only": True,                          # never mutates deps → safe unattended
        "fallback_skill": "codebase-cleanup-deps-audit",
    },
},
```

Schema note: this assumes the entry is a plain dict literal with a `"skill"` string, which is
what `.mex/ROUTER.md` documents ("43 entries in METRIC_CONFIG with 'skill' key"). Confirm the
exact literal in the live file before adding the key — do not restructure the entry.

## Edit 2 — prefer the mechanism in the router (`_nudge_command()` in reflexes.py)

`_nudge_command()` currently builds the nudge from `entry["skill"]`. Make it prefer a
`mechanism` when present, and fall back to the skill on a missing mechanism **or** a
non-zero exit (the genuinely ambiguous tail the plan says to keep LLM):

```python
def _nudge_command(entry: dict) -> dict:
    """Resolve a reflex entry to a runnable command.

    Deterministic mechanism wins when present (fast, testable); the LLM skill is the
    fallback for the judgement tail or when the mechanism exits non-zero.
    """
    mech = entry.get("mechanism")
    if mech:
        return {
            "kind": "mechanism",
            "cmd": mech["cmd"],
            "fallback_skill": mech.get("fallback_skill") or entry.get("skill"),
            "read_only": mech.get("read_only", False),
        }
    return {"kind": "skill", "skill": entry["skill"]}
```

Runner contract (where the reflex engine executes the nudge):
1. If `kind == "mechanism"`: run `cmd`. Exit 0 → record success with `kind:"mechanism"`.
   Non-zero → run `fallback_skill` and record `kind:"skill"`, `fallback_from:"mechanism"`.
2. If `kind == "skill"`: unchanged from today.

## Edit 3 — record the run so the hero metrics see it

On a successful mechanism run, write the exec_log row with `kind:"mechanism"` and emit a
`mechanism_run` event tagged routing-efficient. This is what feeds **Agent Time Saved**
(via `kind` benchmark) and **Cost Savings comp2** (via `routing_efficient`).

exec_log row (same shape as today, plus `kind`):

```json
{"timestamp":"...","command":"bin/codebase_deps_audit.py","skill":"codebase-cleanup-deps-audit",
 "kind":"mechanism","status":"done","source":"reflex_engine",
 "reflex_id":"metric:sword:Deprecated_Deps"}
```

Event emit — note `emit_event.py` currently emits only `pillar/detail/duration_ms`, so it
needs a small additive flag to carry `routing_efficient` and `kind` (stage this too):

```bash
python bin/emit_event.py mechanism_run --pillar sword \
    --detail "Deprecated_Deps via codebase_deps_audit.py" \
    --routing-efficient --kind mechanism
```

```python
# emit_event.py — additive args (do not change existing behaviour)
parser.add_argument("--routing-efficient", action="store_true",
                    help="tag this mechanism_run as routing-efficient (Cost Savings comp2)")
parser.add_argument("--kind", default=None, help='e.g. "mechanism"')
# ...when building `event`, set event["routing_efficient"]=True / event["kind"]=args.kind
# only when provided, keeping the omit-None-fields discipline.
```

## §5 — mirror onto pip-safe-upgrade (ready to apply identically)

`pip-safe-upgrade` fires under `metric:sword:Deprecated_Deps` **and**
`metric:sword:Vulnerability_MTTR` (`state/exec_log.jsonl`). Add the same `mechanism` block
to both Sword entries:

```python
"mechanism": {
    "cmd": ["python", "bin/pip_safe_upgrade.py"],   # add "--apply" only when you want it to upgrade
    "consumes": "~/.claude/data/dependency_audit.json",
    "read_only": True,                               # default (plan-only) is read-only
    "fallback_skill": "pip-safe-upgrade",
},
```

Ordering matters: `codebase_deps_audit.py` **produces** the `dependency_audit.json` that
`pip_safe_upgrade.py` **consumes**, so the deps-audit reflex should run (or be scheduled)
ahead of the pip-safe reflex.

## Apply checklist (live-kernel session)

1. Pull the exact `METRIC_CONFIG` literal for `Deprecated_Deps` and `Vulnerability_MTTR`;
   add the `mechanism` key (Edit 1). Show the diff before writing.
2. Patch `_nudge_command()` + the runner contract (Edit 2). Show the diff.
3. Add the `emit_event.py` flags (Edit 3). Show the diff.
4. Validate: `python execution/doctor.py && python agentica_core/aggregate.py` — no new WARN.
5. Dry-run one cycle with `DOJO_DRYRUN=1`; confirm the reflex runs the mechanism, the
   exec_log row carries `kind:"mechanism"`, and `dependency_audit.json` is written.
6. Watch `state/skill_efficacy.json` `codebase-cleanup-deps-audit` rate over the next cycles
   — the measure→act validation that the switch worked (it should climb off 67%).

## Safety

`codebase_deps_audit.py` is read-only (scans + writes a report; never mutates a dependency)
and its eval includes an idempotency test, so this route is safe to run unattended. The
live-kernel edits themselves are the only step requiring the cautious, show-first protocol —
they are staged here, not applied.
