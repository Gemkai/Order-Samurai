---
title: Arts Output-Quality Evaluation — HANDOFF
date: 2026-07-15
component: agentica_core/evals, Order Samurai Arts pillar
status: COMPLETE (M1–M4 shipped, verified live)
---

# HANDOFF — Arts output-quality evaluation (M1–M4)

## What shipped
The Arts pillar's first **output-quality** metrics — it previously scored only operational hygiene,
never whether an LLM output was actually good. Six LLM-judged metrics, all clean-room (the ELv2
`arize-phoenix-evals` package was never installed, read, or referenced):

| Metric | Group | Judge | Live value (2026-07-15) |
|--------|-------|-------|--------|
| Tool_Selection_Accuracy | arts/Output Quality | local gemma | 90.0 |
| Tool_Arg_Correctness | arts/Output Quality | local gemma | 100.0 |
| Tool_Response_Utilization | arts/Output Quality | local gemma | 66.7 |
| Faithfulness_Score | arts/Output Quality | qwen3.6:35b (red_team, fail-closed) | 50.0 |
| Refusal_Appropriateness | arts/Output Quality | local gemma | 83.3 |
| Retrieval_Relevance | arts/Knowledge | local gemma + Qdrant | 54.2 |

## How it works
- **Substrate** `agentica_core/evals/`: `Score` (honesty type; `kind` heuristic/llm/human, never shows
  `llm` as ground truth), `ClassifierJudge` (fail-closed local; `red_team` escalates the model only,
  never cloud, never a silent gemma4:4b degrade; truncation guard gaps oversize prompts),
  `heuristics.py`, `transcript_source.py`, `tool_triad.py`, `faithfulness.py`, `retrieval.py`.
- **Offline scout** `Order Samurai/bin/tool_quality_scout.py`: loads recent transcripts once, runs all
  judges, writes `state/tool_quality.json`. Retrieval embeds seed queries (nomic-768) → Qdrant
  `claude_skills` top-k → relevance judge. Every remote call has a 15s timeout.
- **Consumption**: cheap REGISTRY reducers in `aggregate.py` (`r_tool_*`, `r_faithfulness`,
  `r_refusal_appropriateness`, `r_retrieval_relevance` — all via the `_tool_quality` factory) READ the
  state file; `None` when absent → SIMULATED. The 15-min dashboard refresh never runs an LLM.
- **Schedule**: folded into the nightly meditation cycle (`bin/meditation_overnight.sh`), non-fatal +
  `tmo 600s`, bounded `TOOL_QUALITY_MAX_JUDGMENTS=15 / MAX_TOOL_USES=20` so it finishes in the box.
- **Honesty tier** `LLM-JUDGED`: new token in `telemetry.py` VALID_TIERS + render.py CSS + METRICS.md
  legend. Distinct from deterministic LIVE; carries a "· llm-judged" badge.
- **Intake**: METRICS.md rows → `replenish_backlog` → `ronin promote` (pillar=arts), never the live
  registry directly.

## Verification (done)
- `agentica_core` 522 passed, `Order Samurai` 687 passed. Judge tests stub the gateway (no live Ollama).
- End-to-end live: scout ran real gemma+qwen+Qdrant → all 6 metrics LLM-JUDGED in wid_payload; absent
  scout → SIMULATED. Full-sample run confirmed the scout completes inside the nightly bounds.

## Rollback
Entirely additive. To revert: `git revert` commits 3dc8e04, 2d90c5b, 5660c57, dc2b53d, 5207b5e (or
delete `agentica_core/evals/`, the scout, the seed JSON; drop the aggregate.py reducers+tuples,
insights.py rows+pillar-map, the telemetry.py/render.py LLM-JUDGED additions, the METRICS.md rows, and
the meditation.sh scout line). No existing metric, reducer, or the reflex/meditation loops are modified;
reducers return None (SIMULATED) if the state file is gone, so a partial rollback degrades gracefully.

## Open / deferred
- **Seed set awaiting ratification**: `config/retrieval_seed_queries.json` (15 draft queries — editable,
  read verbatim by the scout; no code change needed to adjust).
- **Deferred (future +FIELD)**: golden-set exact-match/precision-recall (heuristics written but unwired,
  need labels); under-refusal detection (needs judging every turn); live-traffic retrieval logging (vs
  the current seed-set benchmark).
- **Known cosmetic**: `replenish_backlog.py`'s `next_auto_id` reused id AUTO-041 after a promote (metrics
  are keyed by title/reducer, not backlog id — harmless).
