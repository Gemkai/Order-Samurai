# Cycle Terminology (canon)

_Established 2026-07-18. Three loops, three names — chosen to match what the code already calls
them, so the container and its contents never mean opposite things._

## The three cycles

### Meditation cycle — improves the SYSTEM
One agent-driven remediation run over pillar metrics.
- **Entry:** `bin/ronin-pillar <pillar>` → `prompts/meditation_cycle.md`; write-capable `ronin`
  workers (project agent, Sonnet tier via `CYCLE_MODEL`).
- **Changes:** code, mechanisms, docs — the system itself.
- **Owns:** `state/MEDITATION_STATE.json`, the meditation-api, the dashboard RUN button.

### Sensei cycle — improves TRUST
The 6-hourly verification loop.
- **Entry:** `/sensei-cycle`; read-only `ronin-<pillar>` scouts (Haiku) + `rival` (Fable, Opus
  fallback). Scheduled by `com.agentica.sensei-cycle` (macOS) / `register_sensei_task.ps1` (Win).
- **Changes:** nothing in the repo — it refutes phantom findings, audits `improved:true` claims,
  and posts verdicts to the reflex engine (gate 7b: a REFUTED verdict silences a reflex for 24h).

### Keiko — improves the IMPROVERS
The overnight training window: a batch OF meditation cycles.
- **Entry:** `bin/meditation_overnight.sh` — repeats meditation cycles under a daily budget with a
  flat-cycle early stop (`keiko_improvement.py`).
- **Changes:** this is where batch skill/agent upgrades belong — the skill-improvement cycle
  (below) runs INSIDE keiko, not in the real-time reflex path.

> **Do not repoint "meditation" at agent-improvement.** Every existing artifact (meditation-api,
> `MEDITATION_STATE.json`, `meditation_cycle.md`) already means *system remediation*, and keiko is
> structurally a batch of meditation cycles. Renaming would make the container and its contents
> contradict each other.

## The skill-improvement cycle (lives inside keiko)

Compounding loop that makes the remediation skills better over time, using ground-truth cases the
reflex engine collects for free.

1. **Accumulate (continuous, real-time).** On every mechanism→skill fallback run, the reflex
   engine appends a case to `state/eval_corpus.jsonl`: the deterministic mechanism `diagnosis`,
   the `skill` handed it, the `command`, and the graded `improved` outcome + `files_changed`.
   (`api/src/reflex-engine.ts` `_appendExecLog`.) Nothing is edited; cases just pile up.

2. **Scan / propose (front half, deterministic — BUILT).** Once per keiko,
   `bin/skill_improvement_scan.py` groups the corpus by skill and flags improvement candidates —
   skills with ≥ `min_cases` (default 5) whose improved-rate ≤ `max_rate` (default 0.5), i.e. that
   keep failing *even when handed a correct diagnosis*. Writes
   `state/skill_improvement_candidates.json` with the failing cases' diagnoses attached as eval
   material. Pure arithmetic, no LLM, no-op on an empty corpus. Tested:
   `tests/test_skill_improvement_scan.py`.

3. **Revise + replay + ship (back half, LLM — NOT YET BUILT).** For a top candidate, generate a
   skill revision (skill-optimizer), replay it against the candidate's corpus cases, and ship ONLY
   if the replay pass-rate beats the incumbent. **Gated by human review** — a skill runs with
   `--dangerously-skip-permissions` in the autonomous pipeline, so a revised skill is an unreviewed
   executable until a human approves it. A winning revision should enter DRY-RUN first and earn
   APPLY the same way mechanisms do (maturity ladder). Deferred until the corpus holds real cases —
   building it against an empty corpus would violate "measure before optimizing."

### Why batch, not per-run
Single-run metric moves are noise (the dashboard labels efficacy "correlation not causation"). A
skill edited after every run chases that noise and loses the stable baseline that makes improvement
measurable. Skills therefore *learn from* every run but only *change* when a batch of evidence
beats the current version on replayed evals — the difference between compounding and thrashing.

## Model routing (where each dial lives)
- `REFLEX_MODEL` (reflex engine skill spawns): `~/Library/LaunchAgents/com.agentica.order-samurai-api.plist`
  — the engine reads `process.env` only, so this is its live dial (default sonnet).
- `CYCLE_MODEL`, `RONIN_LOCAL_FALLBACK`, `RIVAL_MODEL`/`RIVAL_MODEL_FALLBACK`: `meditation.env`
  (sourced by the bash fleet: ronin-pillar, ronin-local, sensei-cycle).
- Local-first for bulk work: `bin/ronin-local` uses Ollama, falling back to `RONIN_LOCAL_FALLBACK`
  (haiku) when it's down. Agentic work uses the cheapest cloud tier that clears the bar; only
  rival's judgment call uses the top tier (Fable → Opus on outage).
