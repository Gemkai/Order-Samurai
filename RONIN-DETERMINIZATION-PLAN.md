# Ronin determinization + evals plan — execute in a fresh session

Answers two questions: (1) the dojo has NO formal evals (only observational
before/after + the `rival` adversarial verifier + `skill_efficacy.json`);
(2) ronins should switch MECHANICAL remediations to deterministic mechanisms,
keep LLM for instrumentation + judgment. Determinizing also closes the eval gap
(a deterministic mechanism gets a unit test = a real eval).

## The evidence (from state/skill_efficacy.json, 2026-06-14)
Mechanical remediations run as LLM skills are disproportionately at 0% success;
judgment skills score highest. Determinization should flip the mechanical ones.

| Skill | runs | success | nature | action |
|---|---|---|---|---|
| simplify | 18 | 83% | judgment | KEEP LLM |
| wiki | 13 | 85% | judgment | KEEP LLM |
| audit-mechanisms | 8 | 75% | already wraps mechanism_audit.py | THIN wrapper → call script directly |
| codebase-cleanup-deps-audit | 15 | 67% | mechanical (vuln/license scanners) | DETERMINIZE (run scanners; LLM only for fix judgment) |
| model-selector | 7 | 43% | rule-based routing | DETERMINIZE (routing table) |
| humanizer | 6 | 0% | judgment (prose) | KEEP LLM (investigate why 0%) |
| pip-safe-upgrade | 4 | 0% | mechanical (dry-run/apply/constraint check) | DETERMINIZE — top candidate |
| subagent-audit | 3 | 0% | mechanical (parse session logs by rule) | DETERMINIZE |
| context-optimization | 3 | 0% | judgment | KEEP LLM |
| canary-fault-diagnosis | 2 | 0% | partial (detect deterministic, fix judgment) | SPLIT: deterministic detect + LLM fix |
| policy-enforcement-audit | 2 | 0% | mechanical (grep policy reads vs gates) | DETERMINIZE |
| skill-consolidator | 1 | 0% | mechanical (embedding cosine + threshold) | DETERMINIZE |

## Determinization candidates, prioritized
1. **pip-safe-upgrade** (0%, mechanical, clear logic) — highest ROI; determinizing likely flips 0%→high.
2. **subagent-audit** (0%, log-analysis rules).
3. **policy-enforcement-audit** (0%, structured code scan).
4. **codebase-cleanup-deps-audit** (high-freq 15, mechanical scan core) — biggest volume.
5. **skill-consolidator** (mechanical embeddings).
6. **audit-mechanisms** — already script-backed; just thin the LLM wrapper.
7. **model-selector** — rule-based routing table.
8. **canary-fault-diagnosis** — split deterministic detection from LLM fix.

KEEP LLM (judgment / novelty): instrumentation ronins (bespoke reducers), simplify,
wiki, humanizer, context-optimization, insights.

## The eval scaffold (closes Q1's gap)
For each determinized mechanism:
- **Unit test** with fixtures (input state → expected findings/action) = a real eval the
  LLM skills never had. Lives in `tests/`. This is the cheapest honest eval.
- **Idempotency test** (run twice → same result, no double-action).
For the metric pipeline overall:
- **Regression check:** fixed telemetry fixture → expected `wid_payload` values, so an
  optimization can't silently break another metric (the missing cross-metric guard).
- Keep `rival` as the observational verifier for the remaining LLM remediations.

## How to wire a deterministic mechanism back into the dojo
- The reflex engine currently routes `metric -> skill` (METRIC_CONFIG in reflexes.py).
  Add a `mechanism` route alongside `skill`: if a deterministic mechanism exists for the
  metric, the reflex runs it (fast, testable) instead of spawning an LLM skill; fall back
  to the skill only for the judgment tail.
- `skill_efficacy.json` already tracks success — after determinizing, watch the rate flip
  as the validation that the switch worked (measure→act doctrine).

## Sequence & cautions
1. Pull exact `metric -> skill` mapping from `reflexes.py` METRIC_CONFIG (43 entries).
2. Determinize pip-safe-upgrade first (clearest win) + write its unit test → prove the pattern.
3. Add the `mechanism` route to the reflex engine (live kernel — cautious; show edit first).
4. Roll out the rest by priority; each ships with its eval/test.
- This is multi-file work (skills → scripts, reflex engine, tests). Live-kernel touch on the
  reflex route — fresh session, cautious protocol. Do NOT determinize judgment skills.
