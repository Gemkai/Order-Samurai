---
title: Arts Output-Quality Evaluation — Phase 2 Plan
date: 2026-07-15
problem_type: feature
component: agentica_core/evals, Order Samurai Arts pillar
severity: medium
tags: [evals, arts, llm-judge, clean-room, metric-intake]
status: COMPLETE — M1–M4 shipped. 6 arts output-quality metrics live LLM-JUDGED, scout nightly-scheduled. Retrieval seed set (config/retrieval_seed_queries.json) awaiting user ratification.
---

# Phase 2 Plan — Output-quality evaluation for the Arts pillar

Locked Phase-1 decisions (see memory `arts-output-quality-workstream`): **Option A** (substrate+heuristics →
tool-triad → faithfulness/refusal → retrieval last), **context-only v1** (no golden set), **local-first
judges** (cloud only for the hallucination/faithfulness judge). Clean-room build — `arize-phoenix-evals`
(ELv2) is untouched. **Zero new dependencies in v1** (pure stdlib + the existing `agentica_core/llm/gateway`;
`phoenix-otel` is NOT adopted because no OTel emission is in v1 scope → no `THIRD_PARTY_LICENSES.md` entry needed).

Architecturally independent of the reflex-trustworthiness workstream: this is Python-only under
`agentica_core/`, touches no `reflex-engine.ts`, and does not assume that workstream has landed.

## Global invariants (apply to every milestone)
- **No live-registry wiring without the intake gate.** Each metric: add a `METRICS.md` row → `bin/replenish_backlog.py`
  → `bin/ronin propose` → **human sets `approved:true`** → only then a reducer in the *Governance-copy*
  `ronin_metrics.py` REGISTRY. The approve step is a **HITL gate** — I stop and ask.
- **Honesty ladder.** A reducer returns `-1` for "source missing" (never a fabricated 0). `kind:"llm"` scores
  carry a **"· llm-judged"** badge and never display as ground truth. *Proposed new status-legend token*
  **`LLM-JUDGED`** (distinct from deterministic `LIVE`) — flagged for ratification in M2.
- **Local calls through `local_guards`.** Every `gateway.generate_text` judge call floors `max_tokens>=512`,
  reads the thinking/reasoning fallback, treats empty/unparseable output as failure (→ score kind stays honest,
  metric shows data-gap not a fake pass).
- **Tests green each milestone:** `cd Governance/agentica_core && python3 -m pytest tests/ -q` AND
  `cd "Governance/Order Samurai" && python3 -m pytest tests/ -q`. Judge unit tests stub the gateway (no live Ollama).
- **Rollback:** entirely additive — delete `agentica_core/evals/`, revert the `METRICS.md` rows, drop the new
  REGISTRY entries. No existing metric, reducer, or the reflex loop is modified.

---

## Milestone 1 — `Score` substrate + classifier-judge engine + heuristics library
**Goal:** the reusable clean-room foundation the three judge-based capabilities share. **No metric is wired in
M1** (a consequence of context-only v1: exact-match / precision-recall heuristics need a golden set and are
deferred; the first *wired* metric lands in M2). M1 ships a unit-tested library.

New package `Governance/agentica_core/evals/`:

1. `evals/score.py` — `Score` dataclass `{name:str, score:float, label:str, explanation:str,
   kind:Literal["heuristic","llm","human"], direction:Literal["maximize","minimize","neutral"]}` + a
   `badge()` helper mapping `kind`→honesty label (`heuristic`→"", `llm`→"· llm-judged", `human`→"· verified").
   *verify:* `pytest tests/test_evals_score.py` — construct each kind, assert `badge()` + `direction` round-trip;
   assert an `llm`-kind score can never serialize with a "ground-truth" flag.
2. `evals/heuristics.py` — pure, deterministic, no LLM: `exact_match`, `regex_match`, `contains_all/any`,
   `refusal_markers`, `precision_recall`. All return `Score(kind="heuristic")`. (exact_match / precision_recall
   are written but stay UNWIRED in v1 — they need expected values; documented as golden-set-phase consumers.)
   *verify:* `pytest tests/test_evals_heuristics.py` — table-driven cases incl. empty/None/unicode; P/R math
   against hand-computed values.
3. `evals/judge.py` — `ClassifierJudge(name, template, labels:list[str], label_scores:dict[str,float],
   direction, red_team:bool=False)` with `.evaluate(input, output, context="") -> Score`. Renders
   `{input}/{output}/{context}`, calls `gateway.generate_text(..., response_schema={label∈labels},
   model_chain=<local gemma | qwen3.6:35b-or-cloud if red_team>, max_tokens=floor(...), think=False)`, parses
   the label, maps to score, sets `kind="llm"`. Empty/unparseable → `Score(score=-1, label="error")` (honest gap).
   *verify:* `pytest tests/test_evals_judge.py` with a **stubbed gateway** — valid label → mapped score;
   unknown label → error score; empty content but populated `reasoning` → parsed via local_guards fallback;
   `red_team=True` → asserts the non-local model_chain was requested.
4. `evals/transcript_source.py` — bounded-scan reader over `~/.claude/projects/**/*.jsonl` (reuse the
   `_cache_hit_rate` 60-file mtime-bounded + TTL-cache pattern from `aggregate.py`) yielding normalized turns:
   `{input, output, context, tool_uses:[{name,input,result}], ts}`. Shared data layer for M2/M3.
   *verify:* `pytest tests/test_evals_transcript_source.py` against a synthetic fixture jsonl (mirror
   `test_new_metrics.py:364`'s fixture style) — asserts tool_use/tool_result pairing + output extraction; asserts
   graceful empty result (not crash) when `projects/` is absent.

**M1 exit:** both pytest suites green; new `evals/` package imports cleanly; **no METRIC_CONFIG / REGISTRY /
METRICS.md change yet.** *verify:* `python3 -c "import agentica_core.evals"` + both suites green.

---

## Milestone 2 — Tool-use triad (first LIVE, llm-judged metrics)
**Goal:** three SEPARATE Arts metrics (DRY rule — never one blend), scoring whether the agent used tools well.
Fills the gap where OS counts `Tool_Calls` but never scores them. Data already in transcripts (M1 source).

- `evals/tool_triad.py` — three `ClassifierJudge` instances:
  - `Tool_Selection_Accuracy` — labels `["appropriate","suboptimal","wrong_tool"]` over (task, tool_name, alternatives).
  - `Tool_Arg_Correctness` — `["correct","malformed","wrong_args"]` over (tool_name, tool_input, result_success).
  - `Tool_Response_Utilization` — `["used_well","ignored","misused"]` over (tool_result, subsequent_output).
  Aggregate = mean per-invocation score over the payload window; `direction="maximize"`.
- **Intake (HITL-gated):** 3 rows in `METRICS.md` Arts table `| Metric | Measures | Source | Status |` (Status
  starts `SIMULATED`, flips `LLM-JUDGED` after first live scoring run — *ratify the new legend token here*) →
  `replenish_backlog` → `ronin propose` → **STOP for `approved:true`** → 3 reducers `_tool_selection_accuracy` etc.
  in the Governance-copy REGISTRY (`{pillar:"arts", tier:"AUTO", source:"transcripts:~/.claude/projects"}`) → 3
  rows in `insights.py` METRIC_CONFIG (`dir:"higher"`, warn/fail, weight, the badge).
- *verify (per metric):* unit test scores a fixture transcript with stubbed judge → expected aggregate;
  `refresh_dashboard.py` run shows the metric populated (not data-gap) or an honest `-1`; `Metric_Live_Fraction`
  increments by the number approved. Both pytest suites green.

## Milestone 3 — Faithfulness / refusal judge
**Goal:** two metrics from transcripts, no external truth needed.
- `Faithfulness_Score` — judge (output vs **in-transcript context**); scores ONLY turns where context exists
  (coverage caveat surfaced in the metric envelope, never silently averaged over context-less turns).
  **`red_team=True`** → escalates to qwen3.6:35b/cloud (local 7B rubber-stamps faithfulness — CLAUDE.md caveat).
- `Refusal_Appropriateness` — judge (input vs output); local tier is fine (mechanical).
- Intake identical HITL-gated pipeline → 2 rows → propose → **approve** → 2 reducers → METRIC_CONFIG.
- *verify:* fixture with a known-hallucinated output scores low; a context-less turn is excluded from the
  denominator (assert coverage envelope), not scored 0; both suites green.

## Milestone 4 — Retrieval relevance (+FIELD — re-planned at its boundary)
**Goal:** score whether a retrieved Qdrant chunk supports its query. **Blocked today:** `knowledge_sync.py`
only indexes; no (query, chunk) retrieval log exists.
- **v1 approach = replay-scout (4b), NOT a product-code emitter (4a):** a scout runs a curated seed query set
  against the live Qdrant collection, judges each (query, top-k chunk) pair → `Retrieval_Relevance`. Needs no
  change to product retrieval code; honest tier `SIMULATED`→`LLM-JUDGED` (scoped to the seed set, stated).
  4a (log real retrievals at the query call sites) is the later real-traffic upgrade.
- Gets a full step-level plan at its boundary once M1–M3 land (avoids over-planning a dependent step).

---

## Sequenced HITL gates (where I stop for you)
1. **This plan** — approve before any code.
2. **After M1** — substrate reviewed before wiring the first metric.
3. **Each metric's `approved:true`** in the backlog (M2 ×3, M3 ×2, M4 ×1) — the intake pipeline's human step.
4. **The `LLM-JUDGED` legend token** — ratify at first use (M2).
