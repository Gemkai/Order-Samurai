# Self-Harness Evolution Plan — bounded harness self-improvement for Order Samurai

**Status:** IMPLEMENTED 2026-07-16 (all milestones M1–M6; M4 ships dark behind `SELF_HARNESS_ENABLED`).

**As-built deviations from the text below** (the code and `evalsuite/README.md` are authoritative):
- **M3 tasks were re-grounded to knob sensitivity.** The agent-behaviour groups below (headless-claude
  long-context/leak/tool-vs-guess) are insensitive to every v1 surface knob — a gate that can't detect
  any legal proposal is a spectator. As built: 11 tasks / 4 groups (bounded-scan, judge-pipeline,
  cliff-reducer, loop-breaker-replay), each sensitive to ≥1 declared knob, 7 held-in / 4 held-out,
  miners a2 (held-in) + a4 (held-out, same root cause — the video's A2/A4 pair). Sensitivity was
  verified in both directions against live runs. Cost collapsed from ~108 headless spawns to a
  seconds-fast deterministic pass + 3 local gemma calls.
- **M4 validates by env-override pinning (`OS_HARNESS_<KEY>`), not worktrees** — candidates are pure
  value-sets, nothing writes files during validation, and the escape hatch is the mechanism
  harness_config documented for exactly this. Proposals are key→value edits (structurally narrower
  than diffs); delivery is a ready-to-review file under `state/proposed_surface_edits/` + a pending
  HITL item — no git mutation, no auto-apply. Rival (mode:pre) runs when sensei wiring goes live.
- **M1 shipped 6 knobs, not 8+1**: `judge_max_tokens` live value is 512 (not 1024); the scout windows
  by file count (`scout_max_files`, not days); the meditation instruction clause doesn't exist; the two
  `sensei_*` knobs were dropped because meditation.env already owns them (two sources of truth for one
  number). Values-only v1; a declared-but-unwired-knob test enforces that every knob is really read.
- **M5's tool-trust annotator is deterministic** (no LLM): `corrected`'s obvious proxy measured a 74%
  false-positive rate on 3,286 real calls and was removed; `corrected`/`contradicted` are declared
  `not_implemented`. `abandoned` was renamed `errored_no_retry` (adapting after an error is correct
  behaviour, not a defect). Tool_Retry_Rate entered METRICS.md intake as a NEW metric.
- **M6's prediction prompt suffix defaults OFF** (`REFLEX_PREDICTION_PROMPT`); parsing/recording is
  always on, so `predicted_impact.stated=false` is itself the measurement.

**Original plan text follows (historical):**
**Date:** 2026-07-16
**Sources analyzed:**
- arXiv 2606.09498 — *Self-Harness: Harnesses That Improve Themselves* (Shanghai AI Lab). The concrete algorithm: weakness mining → bounded proposals → two-split regression gate. Validated on Terminal-Bench-2 (+24–104%).
- arXiv 2607.01120 — *Next-Generation Agentic RL Systems Enable Self-Evolving Agents* (Ant Group / AReaL). Position paper: trajectory data protocol (ATDP), data proxy, evolution control plane with intervention routing + replay-first eval + first-class rollback. **No empirical results** — treat as design spec.
- Lilian Weng, *Harness Engineering for Self-Improvement* (2026-07-04). Landscape + design rules: 5-level improvement ladder, observability pillars, permission sandboxing, held-in/held-out gating.
- The Carbon Layer video (KoDohnhLpJM), *Self-Improving AI Agents: Evolving the Harness, Not the Model*. Practitioner run of the Self-Harness loop; contributes hard-won anti-gaming details (per-task acceptance, seeded-file fingerprints, held-out leakage over iterations).

**Audience:** this plan will be executed by a weaker model. Every step is explicit: exact file paths, schemas, commands, and a `verify:` check. Do not improvise beyond a step's scope. If a step's verify fails twice, STOP and report (loop-breaker discipline).

---

## 1. Why this plan exists (gap analysis)

The existing stack already implements most of the papers' "control plane": metric registry → sigma-tiered reflexes → ReflexEngine with verify-gate, worktree staging, maker-checker audit, pytest gate, env-allowlist sandbox → rival adversarial verification → sensei nightly cycle → maturity ladder → skill efficacy cooldowns → HITL queue. That maps 1:1 onto AReaL's pillar 3 and Weng's "evidence-driven iteration."

What is **missing** (confirmed against the live tree 2026-07-16):

| # | Gap | Source concept | Existing nearest thing |
|---|-----|----------------|------------------------|
| G1 | No declared **editable-surface contract** — harness knobs are scattered (env vars, `meditation.env`, hardcoded constants); no self-improvement loop can safely propose edits | Self-Harness §editable surfaces; video's single-config-file | `protected_asset_gate` (blocks, doesn't enable) |
| G2 | No **weakness mining** — reflexes fire on metric thresholds, never on recurring *behavioral failure mechanisms* clustered from traces | Self-Harness Alg. 1 stage 1; failure signature `(cause, causal_status, mechanism)` | `error_triage.py` (error strings, not mechanisms) |
| G3 | No **held-in/held-out eval suite** for harness changes — gates are pytest + audit, which test code correctness, not agent-behavior regression | Self-Harness stage 3; Weng's D_in/D_out rule | pytest gate in reflex worktrees |
| G4 | Trajectory records lack **late-bound rewards, tool-trust labels, and execution-envelope fingerprints** | ATDP fields `r_t` (updatable), tool "later trusted/ignored/corrected/contradicted", harness fingerprint | `exec_log.jsonl` (no fingerprint, no reward annex) |
| G5 | No **decision observability** — code-modifying reflex runs don't record a falsifiable prediction, so post-audit can't score judgment | Weng/AHE "every edit paired with prediction"; ATDP audit record | rival post-audit (checks diff↔claim, not prediction↔outcome) |
| G6 | Proposed-backlog and failed patches are **dead-end sinks** — `state/PROPOSED_BACKLOG.json` accumulates with no consumer | AReaL "rollback/no-op are first-class actions" | dashboard report only |

**What we are deliberately NOT building** (rejected, do not resurrect):
- **AReaL2.0 online-RL / weight updates** — no training infra, single-user Mac, the paper itself reports zero numbers. The routing table's terminal rung ("persistent cross-tenant failure → weights") is out of reach and out of scope; our ladder ends at *skill patch*.
- **Evolutionary search (AlphaEvolve / DGM / population-based)** — token cost is prohibitive for a solo budget; Self-Harness's K-candidate + gate achieves the target with ~1/50th the spend.
- **Meta-harness / level-5 "improve the improver"** — premature until one full loop iteration has run and been reviewed.
- **Full ATDP protocol + multi-tenant governance** — single tenant; we lift only 3 fields (G4), not the protocol.
- **Framework interception proxy** — hooks layer already intercepts the boundaries that matter here.

**Design rules adopted (binding for all milestones):**
1. **Human merges, always.** The loop ends at a PR / HITL entry, never an auto-apply to live config. (Video act 5; AReaL staged deployment; matches existing `control-plane-change` discipline.)
2. **Per-task acceptance, not aggregate.** A candidate is rejected if ANY task goes from passing to failing, even if totals improve. (Video's third-candidate incident: a task flipped always-pass→always-fail while the aggregate barely moved.)
3. **Graders live outside the editable surface** and seeded eval files are fingerprinted at write time; a changed fingerprint = task failure regardless of output. (Video's answer-key-edit trap.)
4. **Not every cluster gets a fix.** Clusters reflecting task difficulty, flakiness, or model capability limits are excluded, not patched. (Self-Harness addressability criterion.)
5. **Expect rejection.** In the paper, ~3–4 accepts per 11–20 iterations. Budget for it; a round with zero accepts is normal, not a failure.
6. **Cheapest surface first.** Missing fact → memory; tool-routing/formatting/instruction failure → harness edit; reusable procedure → skill patch. (AReaL routing table — extends the existing `remediation_kind()` in `agentica_core/insights.py`, which already routes auto_fix/advisory/session_hygiene/mis_route.)

---

## 2. Integration seams (where new code plugs in — do not create parallel mechanisms)

- **New metrics** enter via `Governance/Order Samurai/Research/METRICS.md` intake → replenish_backlog → ronin propose. **NEVER wire the ronin_metrics REGISTRY directly** (LIVE = validated-reducer-only; settled rule).
- **New nightly mechanisms** clone the shape of `Governance/Order Samurai/bin/tool_quality_scout.py` (detect + verify, atomic write to `state/*.json`).
- **State files** are the Python⇄TS contract: write atomically (`tempfile` + `os.replace`) — non-atomic writes to `state/tool_quality.json` already caused an incident (fixed f2158a6).
- **All local-LLM calls** import `agentica_core/llm/local_guards.py` (`floor_max_tokens`, `extract_message_text`). Never re-implement; 47 tests enforce this.
- **LLM judging** reuses `agentica_core/evals/judge.py` (`ClassifierJudge`, local gemma4:12b) and `agentica_core/evals/transcript_source.py` for normalized turns.
- **Protected files**: anything under `~/.claude/` control-plane (settings.json, hooks/, CLAUDE.md, …) is gated. This plan touches ONLY the Governance repo. If a step ever appears to need a `~/.claude` edit, stop and route via the `control-plane-change` skill.
- **Test gates run bare**: `pytest` exit code must be the gate — never `pytest | tail && git commit` (piped exit code masks a red suite; known incident).
- **Commits**: explicit paths only (`git add <paths>`), `git diff --cached --stat` before, `git show --stat HEAD` after (parallel sessions share `work`).

---

## 3. Milestones

Dependency order: M1 → M2 → M3 → M4. M5 and M6 are independent of M4 and can run any time after M1.

### M1 — Editable-surface contract + lineage substrate (size S)

The single file that answers "what is this harness allowed to change about itself," plus the append-only history of every attempted change.

**Step 1.1** Create `Governance/Order Samurai/harness/editable_surface.json`:

```json
{
  "surface_version": 1,
  "description": "The complete editable surface of the Order Samurai harness. A self-harness proposer may ONLY emit diffs against the `values` block of this file. Everything else in the repo is off-limits to automated proposals.",
  "values": {
    "reflex_cooldown_minutes":        {"value": 30,  "type": "int", "min": 10, "max": 240, "consumer": "api/src/reflex-engine.ts"},
    "loop_breaker_limit":             {"value": 2,   "type": "int", "min": 1,  "max": 5,   "consumer": "api/src/reflex-engine.ts"},
    "incomplete_limit":               {"value": 4,   "type": "int", "min": 2,  "max": 10,  "consumer": "api/src/reflex-engine.ts"},
    "sensei_cycle_timeout_seconds":   {"value": 2400,"type": "int", "min": 600,"max": 7200,"consumer": "meditation.env CYCLE_TIMEOUT"},
    "sensei_max_turns":               {"value": 80,  "type": "int", "min": 20, "max": 200, "consumer": "meditation.env MAX_TURNS"},
    "scout_transcript_window_days":   {"value": 7,   "type": "int", "min": 1,  "max": 30,  "consumer": "bin/tool_quality_scout.py"},
    "judge_max_tokens":               {"value": 1024,"type": "int", "min": 512,"max": 4096,"consumer": "agentica_core/evals/judge.py"},
    "context_cliff_token_threshold":  {"value": 140000,"type": "int","min": 80000,"max": 180000,"consumer": "agentica_core/aggregate.py r_context_cliff_events"}
  },
  "instructions": {
    "meditation_verification_clause": {
      "value": "Before concluding, verify the result with the most targeted command, file read, or test you can run.",
      "type": "text", "max_chars": 400,
      "consumer": "prompts/meditation_cycle.md"
    }
  }
}
```

Exact knob list is [ASSUMED-1]; the implementer must confirm each `consumer` reference exists before wiring (grep the named file for the current hardcoded value). If a listed consumer does not exist or the constant has moved, drop that knob from v1 rather than guessing.

**Step 1.2** Create loader `Governance/agentica_core/harness_config.py`:
- `load_surface() -> dict` — reads the JSON, validates every value against its min/max (raise `ValueError` out of range).
- `get_value(key: str) -> Any` — env var override wins (`OS_HARNESS_<KEY_UPPER>`), then file value. Preserves current env-driven behavior exactly.
- `surface_fingerprint() -> str` — sha256 of the canonical-JSON file content, first 12 hex chars.
- → verify: `pytest Governance/agentica_core/tests/ -k harness_config` passes with new tests: load, range rejection, env override, fingerprint stability.

**Step 1.3** Migrate consumers one at a time, behavior-neutral. For each knob: replace the hardcoded constant/env-default with a read via the loader (Python) or via a generated `harness/surface.env` (TS/env consumers — add a tiny `bin/render_surface_env.py` that renders the JSON to env-file lines, called at engine start). Values in the surface file must equal today's live values, so the diff of behavior is zero.
- → verify: after each consumer migration, run that consumer's existing test file bare; full suite green at milestone end; `grep -rn "reflex_cooldown\|LOOP_BREAKER_LIMIT" Governance/` shows reads routed through the loader/env-render.

**Step 1.4** Create lineage ledger writer `Governance/agentica_core/harness_lineage.py` — `append_entry(entry: dict)` doing atomic append to `Governance/Order Samurai/state/harness_lineage.jsonl`. Entry schema:

```json
{"ts": "...", "round": 3, "candidate_id": "r3c2", "diff": "<unified diff text>",
 "audit": {"target_pattern": "...", "surface_keys": ["..."], "expected_effect": "...", "regression_risks": ["..."]},
 "predicted_impact": {"expected_fixes": ["task_a2"], "at_risk": ["task_c1"]},
 "eval": {"held_in_delta": 0.08, "held_out_delta": 0.0, "per_task": {"task_a2": [0.0, 1.0]}, "repeats": 3},
 "decision": "accepted|rejected|structural_reject", "reason": "..."}
```
- → verify: unit test appends two entries, file parses line-by-line as JSON, second append doesn't clobber the first.

**Step 1.5** Stamp the fingerprint into telemetry: every reflex-engine `exec_log.jsonl` entry gains `"harness_fingerprint": "<12hex>"` (read once at engine start from the rendered env). This is the ATDP execution-envelope field — it makes every trace attributable to the exact harness version that produced it.
- → verify: restart the engine (`tsx watch` restart — a saved edit is NOT live until the process reloads; reproduce through the LIVE process), trigger one reflex, confirm the new field in the newest `exec_log.jsonl` row.

### M2 — Weakness-mining scout (size M)

Turns "which number is bad" (existing metric layer) into "which recurring behavioral mechanism keeps failing" (missing trace layer).

**Step 2.1** Create `Governance/Order Samurai/bin/weakness_mining_scout.py`, cloned structurally from `tool_quality_scout.py`. Inputs, over the last `scout_transcript_window_days`:
- failed/incomplete runs from `state/exec_log.jsonl` (status error or incomplete),
- error entries from `Governance/data/pipeline_errors.log`,
- sessions whose final state indicates failure, via `agentica_core/evals/transcript_source.py`.

**Step 2.2** For each failure record, one `ClassifierJudge` call (gemma4:12b, `local_guards` floors) emitting a strict-JSON **failure signature**:
```json
{"terminal_cause": "timeout|missing_artifact|tool_error|verifier_reject|quota|other",
 "causal_status": "causal|incidental|unknown",
 "mechanism": "<snake_case slug, <=4 words, e.g. unbounded_exploration, identical_retry, artifact_deleted, stale_state_read>"}
```
Unparseable judge output = the record is tagged `mechanism: "unattributed"` and excluded from clustering (treat unparseable as failure, never as data).

**Step 2.3** Cluster by **exact match of the full signature tuple** (deterministic — no embeddings, no fuzzy similarity; this is the paper's explicit choice and also what makes the output auditable). Emit atomic write to `Governance/Order Samurai/state/weakness_clusters.json`:
```json
{"generated_at": "...", "window_days": 7, "records_scanned": 214, "records_failed": 31,
 "clusters": [{"signature": {...}, "count": 6, "example_ids": ["...","...","..."],
               "shared_symptoms": "...", "actionable": true,
               "actionability_reason": "recurrent + addressable via editable surface key X"}]}
```
`actionable` is judge-assessed with the rule: a cluster is actionable ONLY if recurrent (count ≥ 3) AND plausibly addressable by a declared surface key or instruction. Flaky one-offs are recorded with `actionable: false` and left alone (they are noise, not a cluster).
- → verify: run the scout over the real last 7 days; output validates against the schema; manually spot-check 3 clusters against their example transcripts — the mechanism slug must describe what actually happened.

**Step 2.4** Metric intake (do NOT wire the registry): append to `Governance/Order Samurai/Research/METRICS.md` two proposed metrics — `Weakness_Cluster_Count` (count of actionable clusters, dir=lower) and `Top_Cluster_Support` (count of the largest actionable cluster, dir=lower) — with reducer sketch "read `state/weakness_clusters.json`", and let the normal replenish_backlog → ronin propose path pick them up.
- → verify: METRICS.md diff contains both entries in the established format of neighboring entries.

**Step 2.5** Schedule nightly after tool_quality_scout (same launchd pattern; remember launchd's bare PATH — set `EnvironmentVariables` PATH in the plist and verify the job's OUTPUT, not just its registration).
- → verify: `launchctl list | grep weakness` shows the job; next-morning `state/weakness_clusters.json` mtime is fresh.

### M3 — Harness eval suite: miners + regression guards (size M)

The yardstick. Without it, M4's proposals cannot be graded. **This whole directory is outside the editable surface.**

**Step 3.1** Create `Governance/Order Samurai/evalsuite/` with:
- `tasks/<id>.json` — 12 tasks [ASSUMED-2] across 4 groups (adopted from the video, grounded in our own observed failures): (a) long-context retention — answer lives near the end of a long seeded file; (b) unproven success claims — task where "done" requires a verifiable artifact; (c) leak checks — seeded secret must not appear in output; (d) tool-vs-guess — question answerable only by actually invoking a tool. Task schema:
```json
{"id": "a2_long_log_tail", "group": "long_context", "kind": "miner|guard",
 "prompt": "...", "seeded_files": [{"path": "...", "content_ref": "seeds/a2.txt"}],
 "grader": "graders.check_a2", "repeats": 3, "timeout_seconds": 120}
```
- `kind: miner` = expected to FAIL today (encodes a known weakness from M2 clusters); `kind: guard` = expected to PASS today (its only job is to fail loudly if a fix breaks it). Populate miners from the top actionable clusters in `state/weakness_clusters.json`; every task must trace to something the harness actually did — nothing invented.
- `graders.py` — deterministic pass/fail functions only (string/file/artifact checks). No LLM grading in v1 (keeps the gate cheap and ungameable).
- `seeds/` — seeded content files. At suite-runner start, sha256 every seeded file after writing it into the task workspace; re-hash after the run; **any changed hash = task fails outright** (answer-key-edit guard).
- `split.json` — `{"held_in": [...7 ids...], "held_out": [...5 ids...]}`, fixed forever once written; held_out ids are NEVER included in any proposer prompt.

**Step 3.2** Create `Governance/Order Samurai/bin/harness_eval_runner.py`:
- Runs each task `repeats` times against the live harness path relevant to the task (headless `claude` spawn with the env-allowlist sandbox pattern from reflex-engine, or direct local-model call for model-behavior tasks [ASSUMED-3]).
- Records **per-task pass fractions** (2/3 is 2/3, not a pass) to `state/harness_eval_baseline.json`: `{"harness_fingerprint": "...", "results": {"a2_long_log_tail": 0.0, ...}, "ts": "..."}`.
- → verify: baseline run completes end-to-end; ≥2 miner tasks are red as designed; all guard tasks green; a second baseline run reproduces each fraction within ±1 repeat.

**Step 3.3** Add a "coupling audit" test: assert no grader or task JSON references any editable-surface value dynamically (the video's own first dry-run failed because a test compared against the live editable value — pin expectations to literals recorded the day the task was written).
- → verify: `pytest evalsuite/tests/test_no_surface_coupling.py` passes.

### M4 — The self-harness cycle (size L) — depends on M1+M2+M3

Algorithm 1 of the paper, adapted: proposer = headless Claude (strong model proposing for the harness — the video's split-role variant), patient = the Order Samurai harness, gate = M3 suite with per-task acceptance.

**Step 4.1** Create `Governance/Order Samurai/bin/self_harness_cycle.py` implementing one round:

1. **Evidence bundle**: top ≤3 actionable clusters from `state/weakness_clusters.json` + list of currently-passing eval tasks ("behaviors to preserve" — ids + one-line description, held_in only) + summaries of prior attempts from `state/harness_lineage.jsonl` (last 10 entries, decision+reason only).
2. **Propose**: spawn headless proposer (`claude -p`, env-allowlist sandbox, same spawn pattern as reflex-engine manual runs) with the bundle + the full text of `editable_surface.json` + the instruction: emit K=3 materially-distinct candidates, each as `{"diff": "<unified diff against harness/editable_surface.json ONLY>", "audit": {...}, "predicted_impact": {"expected_fixes": [...task ids...], "at_risk": [...task ids...]}}`. K=3 [ASSUMED-4].
3. **Structural rejection**: parse each diff; reject immediately (ledger `structural_reject`) if it touches any file other than `editable_surface.json`, violates a knob's min/max, or exceeds `max_chars` on a text surface.
4. **Validate** each surviving candidate: create a fresh worktree (`.tmp/worktrees/` pattern from reflex-engine — note the staging-copy ENOENT incident: exclude nested `state/wt` when copying state), apply the diff, re-render `surface.env`, run the FULL eval suite (both splits, all repeats), then discard the worktree. A candidate never becomes a commit; it exists only as long as its measurement (video's dirty-flag discipline).
5. **Accept** iff: (a) **no individual task's fraction decreases** (per-task rule — stronger than the paper's aggregate rule, adopted deliberately), (b) `Δ_held_in ≥ 0 AND Δ_held_out ≥ 0 AND max(Δ_in, Δ_out) > 0` on averaged fractions. Reject everything else, with reason, to the ledger.
6. **Deliver**: for the best accepted candidate (highest Δ_out, tie-break Δ_in), create branch `harness-evolve/r<N>-<candidate_id>`, commit ONLY `editable_surface.json`, and append an entry to `state/hitl_queue.json` (existing HITL shape) with the per-task before/after table in the description. **No auto-merge. No auto-apply to `work`.**
7. **Adversarial check**: invoke the `rival` agent (mode:pre) on the winning candidate's claim before the HITL entry is written; a REFUTED verdict downgrades the candidate to rejected-with-reason.
- → verify: dry-run with a hand-written synthetic candidate diff (raise `judge_max_tokens` by 1 step) flows through worktree-validate-ledger-HITL end to end; a hand-written malicious diff (touching `graders.py`) is structurally rejected; ledger rows contain every schema field.

**Step 4.2** Wire into sensei-cycle as an optional final stage (env-gated `SELF_HARNESS_ENABLED`, default **off**), budget-guarded by the existing `state/budget_ledger.json` check. One round per night maximum. Cost estimate to record in the ledger: K=3 candidates × 12 tasks × 3 repeats ≈ 108 headless runs + baseline — this is the expensive step; the budget guard must be able to veto it.
- → verify: with the flag off, sensei-cycle behavior is byte-identical; with it on in a manual run, one round executes and the HITL entry renders on the dashboard.

**Step 4.3** Held-out hygiene: after every 5 accepted edits (or 90 days, whichever first), retire ≥1 held-out task and author a replacement from the newest weakness cluster — selection against held-out leaks information into surviving fixes even though the proposer never reads it (video's "quiet cost"). Encode the counter in the ledger; the cycle script prints a loud warning when the threshold is reached. Rotation itself is a human action.
- → verify: unit test on the counter logic.

### M5 — ATDP-lite trajectory enrichment (size M) — independent of M4

Two fields from the AReaL protocol that directly feed metrics we already compute.

**Step 5.1** Tool-trust annotator `Governance/Order Samurai/bin/tool_trust_annotator.py`: nightly post-hoc pass over the transcript window (via `transcript_source.py`); for each tool result, classify from the FOLLOWING turns whether it was **trusted / ignored / corrected / contradicted** (one ClassifierJudge call per tool result batch, strict JSON). Atomic write to `state/tool_trust.json`. Consumer: `agentica_core/evals/tool_triad.py` — `Tool_Response_Utilization` gains a grounded signal (downstream-use evidence) instead of judge-only inference; blend rule: heuristic label overrides judge when they conflict [ASSUMED-5].
- → verify: run on 20 sessions; hand-check 10 labels ≥8 agree; tool_triad tests still green.

**Step 5.2** Late-bound reward annex: `Governance/agentica_core/reward_annex.py` with `attach(session_id, signal_type, value, source)` appending to `state/reward_annex.jsonl` — the original trace stays immutable; rewards land later in a side-table (ATDP principle 4). Initial producers (wire only these two): session outcome from `session_finalizer.py` telemetry already emitted to Governance, and eval-suite results keyed by fingerprint. Consumer: `agentica_core/remediation.py` efficacy correlation reads the annex as an additional improvement signal.
- → verify: unit tests for append/read; `remediation.py` full test file green bare.

### M6 — Decision observability (size S) — independent, do after M1

**Step 6.1** Extend the reflex-engine exec-log schema: every **code-modifying** reflex run must include `predicted_impact: {expected_fixes: [...], at_risk: [...]}` (extracted from the spawned agent's structured output; `"unstated"` allowed but counted). TS type change in `api/src/types.ts` + write site in `reflex-engine.ts`.

**Step 6.2** Extend the sensei post-audit prompt (rival mode:post) to score prediction vs outcome: did the predicted fixes materialize, did the at-risk items regress, or was the prediction absent/wrong? Verdict lands in `SENSEI_LEDGER.jsonl` as a new `prediction_grade: accurate|partial|wrong|unstated` field. This gives `maturity.py` a future judgment signal (wiring maturity to consume it is a later phase — for now, record only).
- → verify: manual reflex run shows the field in `exec_log.jsonl`; one simulated post-audit produces a `prediction_grade`.

---

## 4. Rollout, risk, rollback

- **Order**: M1 → M2 → M3 → (M6, M5 anytime) → M4. M4 ships dark (`SELF_HARNESS_ENABLED=off`) and is turned on manually for its first supervised round.
- **Rollback**: M1 is behavior-neutral by construction (values copied from live). Every accepted M4 edit is a one-file diff on a branch — revert = don't merge, or `git revert` the single commit. The lineage ledger is the audit trail AReaL demands ("an agent that cannot explain what changed is merely drifting").
- **Reward-hacking surface**: graders + seeds + split.json + lineage ledger are read-only to the proposer by construction (structural rejection) and should ALSO be added to the maker-checker audit's suspicious-path list in `execution/audit_remediation_patch.py` (defense in depth).
- **Cost control**: the only expensive component is M4 validation (~100+ headless runs/round). The budget guard vetoes; the round cap is 1/night; expect most rounds to accept nothing (paper's observed accept rate ~20%).
- **Failure honesty**: if the eval suite itself is flaky (a guard task fraction oscillates across baseline re-runs), fix or remove the task before trusting any round — until the suite is stable it is a guess, not a yardstick.

## 5. Assumptions to ratify (numbered — reply by number)

1. **[ASSUMED-1]** The initial editable surface is the 8 numeric knobs + 1 instruction clause listed in Step 1.1 (implementer verifies each consumer exists, drops unverifiable ones). Alternative: start values-only (no instruction surfaces) for an even safer v1.
2. **[ASSUMED-2]** Suite size 12 tasks / 4 groups / 3 repeats, 7 held-in / 5 held-out.
3. **[ASSUMED-3]** Eval tasks run against headless `claude` spawns (harness behavior), not the local Ollama tier — we are evolving the Governance harness, not the local models.
4. **[ASSUMED-4]** K=3 proposal candidates per round, 1 round/night max.
5. **[ASSUMED-5]** Tool-trust heuristic label overrides the LLM judge on conflict in `Tool_Response_Utilization`.
6. **[ASSUMED-6]** New metrics go through the METRICS.md intake path only; no registry wiring in this plan.
