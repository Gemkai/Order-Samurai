## Handoff — 2026-06-17 Wire Tier-1 mechanism route into live reflex engine

**Branch (Order Samurai):** `feat/dojo-mechanism-route`
**Branch (Governance):** `feat/hero-metrics-honesty`
**Plan followed:** `RONIN-MECHANISM-ROUTE-PLAN.md` (v2, verified)

---

### Files changed

| Repo | File | Commit | Reason |
|------|------|--------|--------|
| Governance | `agentica_core/insights.py` | `2c2b66d` | Add `mechanism` blocks to 4 Tier-1 METRIC_CONFIG entries + propagate through `annotate()` → env |
| Governance | `agentica_core/reflexes.py` | `2c2b66d` | Carry `mitigation_mechanism` from env into the reflex dict so wid_payload.json contains the `mechanism` field |
| Governance | `api/src/reflex-engine.ts` | `c1b57c1` | Add `MechanismBlock` interface, `mechanism?` to `ReflexEntry`, `_runMechanism()` helper (kill-ladder shape mirrors `runFallback`), mechanism branch in `_execute()`, `kind`/`fallback_from` fields on exec_log rows |
| Order Samurai | `bin/emit_event.py` | `01f75ca` | Add `--routing-efficient` (store_true) and `--kind` (default None) flags; omit-None pattern |

### Tier-1 wiring summary

| Metric | Script | Fallback skill |
|--------|--------|----------------|
| `Deprecated_Deps` | `bin/codebase_deps_audit.py` | `/pip-safe-upgrade` |
| `Rule_Violations` | `bin/policy_enforcement_audit.py` | `/policy-enforcement-audit` |
| `Canary_Failures` | `bin/canary_fault_detect.py` | `/canary-fault-diagnosis` |
| `Gate_Canary_Fault` | `bin/canary_fault_detect.py` | `/canary-fault-diagnosis` |
| `Subagent_Efficiency_Index` | `bin/subagent_audit.py` | `/subagent-audit` |

### Build status

```
npx tsc --noEmit (Governance/api/)  →  EXIT 0  (TypeScript clean, no errors)
python -m pytest tests/ -q          →  216 passed, 81 subtests passed in 0.38s
```

---

### Remaining human steps (in order)

These are DEFERRED — do not run until the next session with a human present.

**1. Restart the Governance API server** (picks up TS changes)
```
# Stop tsx watch, then:
cd "C:\Users\jemak\Desktop\Agentica OS\Governance\api"
npm run dev
```
`tsx watch` does NOT auto-reload on branch changes — it must be restarted to pick up
the new `api/src/reflex-engine.ts` after the `feat/hero-metrics-honesty` commit.

**2. Run doctor + aggregate to confirm no new WARNs**
```
cd "C:\Users\jemak\Desktop\Agentica OS\Governance"
python execution/doctor.py && python agentica_core/aggregate.py
```

**3. Confirm mechanism field survives the full serialize/deserialize cycle**
```
# After server restart, run a dashboard refresh:
python refresh_dashboard.py
# Then inspect wid_payload.json — any active Tier-1 reflex should show a `mechanism` key:
python -c "
import json, pathlib
wid = json.loads(pathlib.Path('state/wid_payload.json').read_text())
mechs = [r for r in wid.get('reflexes', []) if 'mechanism' in r]
print(f'{len(mechs)} reflexes with mechanism field')
for r in mechs[:3]: print(' ', r['id'], '->', r['mechanism']['script'])
"
```

**4. Force a reflex cycle and confirm exec_log kind:"mechanism"**
```
# Either wait for the natural reflex cycle, or temporarily lower the cooldown.
# After a run, check:
python -c "
import json
rows = [json.loads(l) for l in open('state/exec_log.jsonl') if l.strip()]
mech_rows = [r for r in rows if r.get('kind') == 'mechanism']
print(f'{len(mech_rows)} mechanism exec_log rows')
for r in mech_rows[-3:]: print(' ', r.get('reflex_id'), r.get('status'))
"
```

**5. Test the non-zero-exit fallback (plan §6 step 6)**
```
# Temporarily rename one mechanism script to make it fail, force a reflex,
# then verify the skill was invoked and exec_log shows fallback_from:'mechanism'.
# Restore the script after verification.
```

---

### Rollback

Governance repo (revert TS + Python changes):
```
git -C "C:/Users/jemak/Desktop/Agentica OS/Governance" revert c1b57c1 2c2b66d --no-edit
```

Order Samurai repo (revert emit_event.py):
```
git revert 01f75ca --no-edit
```

To fully remove the branch instead:
```
git branch -D feat/dojo-mechanism-route   # in Order Samurai (after switching away)
```

---

### Behavioral note (set expectations)

All 4 Tier-1 mechanisms are **detect/report** only — they write findings but do NOT clear
the metric (their bin/*.py scripts don't mutate system state). Therefore `improved` in
exec_log will remain `false` after mechanism runs, same as the LLM skill. The value is
**speed + cost (no LLM call) + deterministic verdict**. Efficacy numbers will NOT climb
for detect-only mechanisms — measure them on verdict correctness, not `improved` rate.
See RONIN-MECHANISM-ROUTE-PLAN.md §4 for full explanation.

---

### 5 grep checks

| # | Check | Result |
|---|-------|--------|
| 1 | No raw user input in subprocess args | PASS — mechanism script path assembled in TS from trusted METRIC_CONFIG, not user input |
| 2 | No secrets in log calls | PASS — no `console.log`/`logger.` with secrets; emit_event writes to JSONL, no secrets in fields |
| 3 | Every new route has auth middleware | N/A — no new routes |
| 4 | Every new env var is read in code | N/A — no new env vars |
| 5 | Every changed line traces to stated goal | PASS — all changes are mechanism routing infrastructure |

### Open risks

- Governance API server must be restarted for TS changes to take effect — tsx watch only picks up source file saves when the process is live, not after branch switches.
- If `feat/reflex-mechanism-route` is ever rebased/merged into `feat/hero-metrics-honesty`, the mechanism blocks in METRIC_CONFIG may conflict (the earlier branch only wired `Deprecated_Deps` → `pip_safe_upgrade.py`; this commit wires 4 Tier-1 scripts). Resolve by keeping the Tier-1 entries from this commit.
- `emit_event.py` flags added but NOT yet called from `_runMechanism` in reflex-engine.ts — calling `emit_event mechanism_run --kind mechanism --routing-efficient` from the TS spawn is a follow-up wiring step deferred to the next session.

---

