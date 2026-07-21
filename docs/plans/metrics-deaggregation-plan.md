# Plan — De-aggregate Order Samurai metrics (move off rollup scores)

**Status:** DRAFT — for engineering review · **Date:** 2026-06-19
**Owner:** Gemkai · **Author:** Claude (Opus 4.8)
**Motivation:** The owner is deliberately moving away from aggregate/composite metric
scores because the software-engineering industry has moved off them. This plan turns
that decision into a concrete, phased redesign of Order Samurai's metric surfaces.

---

## 1. Research basis (lessons learned, cited)

Sourced via `/deep-research` (18 sources, 25 extracted claims). **Verification caveat:**
the adversarial-verification pass was rate-limited (every vote `0-0 abstain`), so the
harness labeled the run "inconclusive." The extracted claims are from authoritative
primary sources and corroborated by public record; re-verification is pending.

1. **A single number destroys diagnostic signal** — a composite "gives no insight into
   *why* one item scores higher than another" and "masks differences and relationships
   between indicators." ([getDX](https://newsletter.getdx.com/p/developer-productivity-metrics),
   [NCBI PMC9098058](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9098058/))
2. **Arbitrary weighting = false precision** — composites are "sensitive to construction
   methodology; non-transparent construction renders rankings unreliable and prone to
   misuse." (NCBI)
3. **Goodhart's law / gaming** — "Measuring … changes behavior, including inventing ways
   to improve the measurement score itself at the expense of actual results."
   ([Pragmatic Engineer](https://newsletter.pragmaticengineer.com/p/measuring-developer-productivity)).
   Acute risk for a *self-governing agentic system* optimizing its own score.
4. **McKinsey backlash** — Beck & Orosz judged single effort/output scoring **net-harmful**;
   consensus replacement is **balanced multi-dimensional sets, not rollups.**
   ([LeadDev](https://leaddev.com/career-development/what-mckinsey-got-wrong-about-developer-productivity))
5. **Balanced sets in deliberate tension** — DORA: no "one metric to rule them all"
   ([dora.dev](https://dora.dev/guides/dora-metrics-four-keys/)); SPACE: 5 dimensions, never
   aggregated; DX Core 4: 4 counterbalanced dimensions, speed **equal-weighted against** an
   experience counter-metric *to prevent gaming*
   ([getDX research](https://getdx.com/research/measuring-developer-productivity-with-the-dx-core-4/)).
6. **Guardrail / counter-metrics** — pair throughput with a quality/cost counter so
   optimizing one can't silently degrade another. ([Mixpanel](https://mixpanel.com/blog/guardrail-metrics/))
7. **Outcome over effort/output** — effort metrics create gaming incentives; measure
   outcome/impact. (= Order Samurai's own Measure→Act doctrine.)
8. **SLO-style status + trend per metric** — show target status (meeting / at-risk /
   breached) + direction, not a grade.
   ([SLO dashboards](https://www.agileanalytics.cloud/blog/slo-dashboards-that-tell-a-story-what-to-visualise-and-what-to-avoid))
9. **When a bounded composite is still OK** — transparent construction, decomposable on
   click, a *conversation-starter not a target*, **not tied to incentives**. A breach
   **count** qualifies; a quality **grade** does not.

---

## 2. Current state — three aggregation layers

| Layer | Where | What it does |
|---|---|---|
| **A. 100-pt architecture score** | `execution/score_architecture.py` (OS), `config/architecture_scorecard.json` | 8 weighted categories → one 0–100 (currently 60/100) |
| **B. 4 pillar rollups** | `Governance/agentica_core/aggregate.py` `build_pillars()`, hero metrics; `dashboard-ui` | ~13 metrics/pillar → a pillar-level grade/hero number |
| **C. Metric weights 1/2/3** | `Governance/agentica_core/insights.py` `METRIC_CONFIG` (38 of 53 metrics) | multipliers feeding the rollups |

Per-metric `warn`/`fail` thresholds already exist on 38 metrics — this is the SLO-style
substrate the research endorses; the rollups sit *on top* of it.

---

## 3. Redesign principles

- **Keep dimensions, drop rollup scores.** The 4 pillars stay as the *organizing balanced
  set* (this is literally SPACE/DX-Core-4); they stop emitting a grade.
- **Status + trend per metric is the primary surface** (vs. thresholds you already have).
- **Exactly one legitimate composite:** a transparent, decomposable **"needs-attention" count**.
- **Make tension explicit** via guardrail pairs, never blended.
- **Outcome > activity.** Foreground remediation efficacy; de-emphasize vanity counts.

---

## 4. Phased plan

### Phase 1 — Status-first surfaces (foundation)
- **Goal:** every metric shows status (OK/WARN/FAIL vs its threshold) + 30-day trend; no rollup.
- **Files:** `dashboard-ui/src/*` (pillar panels → per-metric status rows); `aggregate.py`
  (ensure per-metric status + trend series are in the payload — thresholds already there).
- **Design layer (from `/plan-design-review`):**
  - **Hierarchy (replaces grade-as-anchor):** (1) needs-attention fires → (2) per-pillar status
    grids → (3) healthy + informational metrics collapsed. The fires list is the new visual anchor.
  - **Status encoding:** color **+ shape** (colorblind-safe), not color alone; dense utility
    table, not fat stat-cards (avoid Grafana-clone slop). Reuse pillar colors (`var(--bow)` …).
  - **Sparkline:** 30-day, with the **threshold line drawn inside** so distance-to-breach is visible.
  - **States (every one is a feature):** all-clear empty state ("0 need attention" = success, not
    blank); no-data/uncalibrated → `—` not a false `0`; informational metrics → **neutral** badge
    (not green — they're untargeted, not "passing"); loading.
  - **STALE-DATA INDICATOR (REQUIRED — born from the 2026-06-14 4-day outage):** a "last refreshed /
    data age" badge + stale warning when the payload is older than a threshold. The dashboard silently
    showed 4-day-old data during the outage; staleness must be *visible*.
- **Verify:** each pillar panel renders N metric rows with status badge + sparkline; no pillar grade
  shown; all-clear, no-data, and stale states each render distinctly.

### Phase 2 — "Needs-Attention" count (the one composite) + anti-gaming guards
- **Goal:** top-level signal = count of WARN+FAIL metrics, decomposable to the sorted list
  (by severity, then weight as a *sort hint only*).
- **Anti-gaming guards (REQUIRED — this is a self-governing agentic system; the count is
  an implicit incentive the reflex engine optimizes against):**
  1. The count **never drives a reflex or grade** — only individual per-metric thresholds
     drive remediation (already true; assert it stays true).
  2. **Threshold / METRIC_CONFIG changes are written to an audit trail** so the agent cannot
     silently loosen a threshold (or suppress a metric) to drop the count.
  3. The **full breaching list is always rendered beside the count** — the number is never
     a standalone KPI.
- **Design (from `/plan-design-review`):** render the count as a **triage label** ("3 need attention →"
  beside the always-expanded list), **NOT a hero KPI number** — a giant "3/53" stat visually re-creates
  the aggregate score being removed.
- **Files:** `aggregate.py` (emit `needs_attention: [{metric, status, pillar, severity}]` +
  count); `dashboard-ui` header; new threshold-change audit log (e.g. `state/threshold_audit.jsonl`).
- **Tests:** unit-test the WARN/FAIL→status mapping and the count; test that the audit log
  captures a threshold edit.
- **Verify:** count == number of breaching metrics; clicking expands the exact list; no graded
  number in the header; a threshold edit appears in the audit log.

### Phase 3 — Explicit guardrail pairs
- **Goal:** surface counterbalanced metrics side-by-side; flag when one improves while its
  counter degrades (e.g. throughput↑ + Slop_Density↑).
- **Files:** new `agentica_core` pairing config (throughput↔quality↔cost); `dashboard-ui` tension widget.
- **Verify:** a forced throughput-up/quality-down state renders a tension warning.

### Phase 4 — Outcome over activity
- **Goal:** foreground remediation efficacy (did the fix move the metric — already recorded);
  de-emphasize raw activity counts in the primary view.
- **Files:** `dashboard-ui` ordering/emphasis; possibly tag metrics `kind: activity|outcome` in `METRIC_CONFIG`.
- **Verify:** outcome metrics surface above activity counts; activity counts demoted/secondary.

### Phase 5 — Demote the architecture score
- **Goal:** `score_architecture.py`'s per-category PASS/blocking/advisory-gap table becomes the
  primary view; the 60/100 becomes a secondary, click-to-expand figure (kept per lesson #9, not removed).
- **Files:** `dashboard-ui` architecture panel; `score_architecture.py` output already has the breakdown.
- **Verify:** category statuses are the headline; the single number is collapsed by default.

---

## 5. Keep / Out of scope

**Keep:** per-metric `warn`/`fail` thresholds (right model); the 4 pillars as *dimensions*;
all deterministic-mechanism / opt-in-grant work (that's **acting on** metrics — orthogonal to
**aggregating** them).

**Out of scope:** changing what each metric *measures*; the reflex-engine grant gate; deleting
the architecture-score number outright (we demote, not delete).

---

## 6. Open questions (for review)

1. **Scope:** all of A+B+C, or start with A+B (kill the architecture grade + pillar grades) and defer C?
2. ~~Zero top-level numbers vs. breach-count~~ — **RESOLVED 2026-06-19:** breach-count + anti-gaming
   guards (confirmed by owner; see Phase 2 + §8 finding 1).
3. **Weights:** keep `weight` purely as a sort/priority hint, or remove the field entirely?

## 7. Rollback
Each phase is additive + reversible: the rollup computations are demoted/hidden, not deleted, until
the status-first surfaces are validated. Revert = re-show the rollup, drop the new panels.

---

## 8. Review outcome (`/plan-eng-review`, 2026-06-19)

**Scope challenge result:** the redesign is mostly **re-presentation, not rebuild** — per-metric
history already exists (`Data/telemetry/metrics_history.jsonl`), thresholds exist on 38 metrics,
`score_architecture.py` already emits the per-category breakdown, remediation efficacy is already
tracked. Low blast radius (dashboard-ui + small payload additions).

**Findings folded in:**
1. **[CONFIRMED by owner 2026-06-19] Needs-attention count = gaming trap** → Phase 2 carries
   anti-gaming guards (count never drives a reflex, threshold edits audited, list always shown).
2. **[scope added] Composite-score *metrics* inside the config:** `Architecture_Scorecard_Grade`
   (warn 85/fail 70, weight 3) and `Security_Scorecard` are themselves aggregate scores baked into
   pillars (`aggregate.py:1310`, `insights.py:54`). Keep them as **bounded composites with their
   thresholds** (transparent + decomposable), demote the headline, always link to the breakdown.
3. **[handling] 10 metrics have no thresholds** (Total_Cost, Token_Spend, Skills_Optimized, Tool_Calls,
   Cost_Per_Task, Revision_Ratio, Skill_Promotions, Agent_Process_Count, Complexity_Weighted_Throughput,
   Instrumentation_Coverage) → render as **"informational / no-target"**; do NOT invent thresholds.
4. **[confirmed] demote-not-delete** preserves history + the downstream `Architecture_Scorecard_Grade`
   metric/reflex. Keep computing rollups, hide in UI.
5. **[sequencing] MVP first = Phase 5 + Phase 1-status** (drop the architecture headline + pillar grades,
   show per-metric status+trend). Phases 3–4 are follow-ons.
6. **[tests] each phase ships unit tests** for new payload logic (status mapping, count, pairing tension).

## NOT in scope
- Changing what each metric *measures* (only how it's presented/aggregated).
- The reflex-engine grant gate / mechanism work (orthogonal — that's *acting on* metrics).
- Deleting the architecture score or pillar rollups outright (we demote/hide, preserving history).
- Inventing thresholds for the 10 observational metrics (would reintroduce false precision).

## What already exists (reused, not rebuilt)
- `Data/telemetry/metrics_history.jsonl` — per-metric time series → trend sparklines.
- `METRIC_CONFIG` warn/fail thresholds (38 metrics) → per-metric SLO status.
- `score_architecture.py` per-category PASS/blocking/advisory-gap breakdown → decomposed architecture view.
- Remediation-efficacy tracking → the outcome signal for Phase 4.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | clean | 6 findings folded in; decision CONFIRMED; 0 unresolved; 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | clean | score 5/10 → 8/10; key adds: needs-attention-as-anchor hierarchy, REQUIRED stale-data indicator (outage-driven), fires-first dense anti-slop, count-as-triage-label not hero KPI |

**UNRESOLVED:** 0 — the needs-attention-signal decision is confirmed (count + anti-gaming guards).
**VERDICT: CLEARED (Eng + Design)** — plan is sound, de-risked, internally consistent; mostly re-presentation.
Ship **Phase 5 + Phase 1-status first**. Genuinely new logic = Phase 2's threshold-change audit log. Design
layer folded into Phase 1 (hierarchy, states, stale indicator, anti-slop).
