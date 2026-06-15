# Hero-metric honesty plan — execute in a fresh, compacted session

Context: the 4 pillar hero metrics. Kill Chains is real. Agent Time Saved is calibratable
(forward samples, threshold now 10, dispatch-stamping fixed). Cost Savings and Human Time
Saved are `real volume × estimate coefficient` with no per-unit sample source — their
"calibrated" flag can NEVER flip under the current mechanism (`_calibrate_coefficients`
only writes the `operations` block). This plan makes them HONEST and, where possible, REAL.

All edits are in the **LIVE Governance kernel** `C:\Users\jemak\Desktop\Agentica OS\Governance\agentica_core\aggregate.py`
and the frontend `Governance/dashboard-ui/`. NOT the frozen repo-local kernel. The drift
tripwire only checks `_parse_iso`/`_calibrate_coefficients`, so these reducers are safe to edit.

## 1. Cost Savings — reframe to its real core (recommended: becomes a genuine metric)
`_estimated_cost_savings` is two parts: comp1 = `(prior_cpt − this_cpt) × n_tasks` (REAL,
from `total_cost` telemetry) + comp2 = `efficient_runs × $0.05` (estimate coefficient, no
per-event $ sample). Today comp2 ≈ 0 (data_gap), but it *drags* the calibrated flag to
permanent False via `calibrated = comp1_calibrated and comp2_calibrated`.
- **Drop comp2 from the dollar metric:** `val = comp1_savings`; `calibrated = comp1_calibrated`.
- **Fix the week_delta honestly:** currently `week_delta = val − last_week_comp2_savings`
  — with comp2 gone this is inconsistent. Compute last-week comp1 (prior-vs-prior-prior
  cost-per-task) for a true delta, OR set `week_delta = 0.0` with a comment until that's wired.
- **Optional:** surface routing efficiency as its own REAL count metric ("efficient routings:
  N") instead of fake dollars — the count is real; the $/event is not.
- **Rename** `Estimated_Cost_Savings` → `Cost_Per_Task_Savings` (REGISTRY + DOJO_STATE +
  frontend label) since it's now measured, not estimated. (Bigger touch — do last.)
- verify: refresh_dashboard.py; Cost Savings shows `calibrated: True` driven by real cost data.

## 2. Human Time Saved — no real core; honest treatment only
All 4 craft coefficients (vibe/doc/promo/effort → hours) are estimate-conversions with no
sample source. Two honest options (pick one):
- **(A) Relabel (lighter):** keep the rolled-up number, change the UI from "awaiting
  calibration" to "estimate — by design (rolled up from real metrics, not measured)".
  Frontend only (`dashboard-ui/.../helpers.tsx` ScoreNumber indicator + `types.ts`).
- **(B) Replace (truer):** drop the synthetic hours; surface the REAL underlying improvements
  directly (Vibe_Alignment Δ, Doc_Parity Δ, promotions, arts effort) — which already exist as
  metrics. Reducer + frontend. More work; most honest.
- verify: dashboard shows either an honestly-labeled estimate or the real component deltas.

## 3. Lever B — honest labeling everywhere (frontend)
The `calibrated: false` indicator currently implies "interim, will clear." For Cost Savings
(after #1 it clears for real) and Human Time Saved (never clears), the UI must distinguish:
- "awaiting samples" (Agent Time Saved — will calibrate as the dojo runs), vs
- "estimate by design" (Human Time Saved — permanent; no sample source).
Touch points: `dashboard-ui/src/.../helpers.tsx`, `types.ts` (PILLARS headline calibration
indicator). Do NOT invent empirical "bases" for the seed coefficients — honestly mark them
unvalidated, not justified after the fact.

## Sequence & cautions
1. #1 Cost Savings reducer (kernel) — highest value, turns a permanent-fake into a real metric.
2. #3 UI label distinction (frontend) — makes the remaining estimate honest.
3. #2 Human Time Saved — pick A or B with the user.
4. #1 rename + routing-count metric — last, biggest blast radius.
- Live kernel: show each edit before applying; re-run refresh_dashboard.py after each; the
  frozen repo-local kernel and the drift tripwire are NOT affected by these reducers.
- Frontend: this session never inspected `dashboard-ui/` — scope it first.
