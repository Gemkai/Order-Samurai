# Sensei-Cycle E2E Runbook

Manual verification playbook for the full Ronin→Rival→Sensei loop. Run these steps in order after any structural change to sensei-cycle, the ReflexEngine verdict gate, or the ronin/rival agents.

**Prerequisites:**
- Order Samurai API running: `cd Governance && npm run dev` (port 3001)
- `ORDER_SAMURAI_ROOT` set to `C:\Users\jemak\Desktop\Projects\Order Samurai`
- SENSEI_LEDGER.jsonl exists (may be empty): `state\SENSEI_LEDGER.jsonl`

---

## Scenario 1 — Real reflex, dry-run, no POST

**Goal:** Scout re-measures, rival CONFIRMS, ledger written, no POST to engine.

1. Seed synthetic telemetry with a sword reflex:
   ```python
   # seed_sword_reflex.py
   import json, datetime, pathlib
   ROOT = pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai")
   payload = json.loads((ROOT / "Governance\state\wid_payload.json").read_text())
   # Inject a CRITICAL Rule_Violations reflex
   payload.setdefault("reflexes", []).append({
       "id": "metric:sword:Rule_Violations",
       "tier": "CRITICAL",
       "status": "active",
       "pillar": "sword",
       "snapshot_value": 5,
       "mitigation_command": "/policy-enforcement-audit",
       "seeded_at": datetime.datetime.utcnow().isoformat() + "Z",
       "_test_seed": True
   })
   (ROOT / "Governance\state\wid_payload.json").write_text(json.dumps(payload, indent=2))
   print("Seeded metric:sword:Rule_Violations CRITICAL reflex")
   ```

2. Run dry-run cycle:
   ```
   claude --print -p "/sensei-cycle --dry-run" --permission-mode acceptEdits --max-turns 40
   ```

3. **Expected output:**
   ```
   [sensei-cycle --dry-run] cycle_id=<uuid>
   Reflexes: 1 total, 1 CRITICAL, 0 HIGH
   Scouts: sword=1 findings
   Rival: CONFIRMED=1 REFUTED=0 SUSPECT=0
   Ledger: 1 rows written (dry-run: no POST, no backlog)
   ```

4. **Verify ledger row written:**
   ```python
   import json, pathlib
   rows = [json.loads(l) for l in (pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai\state\SENSEI_LEDGER.jsonl")).read_text().splitlines() if l.strip()]
   assert any(r["reflex_id"] == "metric:sword:Rule_Violations" and r["rival_verdict"] == "CONFIRMED" for r in rows), "FAIL: no CONFIRMED ledger row"
   print("PASS: CONFIRMED ledger row found")
   ```

5. **Verify NO POST to engine** (ledger shows dry-run, engine endpoint not called):
   - Check `state\reflex_verdicts.json` — should NOT have a new entry for the seeded reflex_id (unless a prior cycle ran).

---

## Scenario 2 — Full cycle, engine executes

**Goal:** Verified reflex flows through ReflexEngine and fires remediation.

1. Ensure the seeded reflex from Scenario 1 is still present in `wid_payload.json`.

2. Run full cycle (no --dry-run):
   ```
   claude --print -p "/sensei-cycle" --permission-mode acceptEdits --max-turns 40
   ```

3. **Expected:** Rival CONFIRMED → POST to `/api/reflex/verdicts` → engine receives CONFIRMED verdict → `_isEligible()` passes → skill fires → `exec_log.jsonl` has new entry.

4. **Verify exec_log entry:**
   ```python
   import json, pathlib
   rows = [json.loads(l) for l in (pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai\state\exec_log.jsonl")).read_text().splitlines() if l.strip()]
   seed_rows = [r for r in rows if r.get("reflex_id") == "metric:sword:Rule_Violations"]
   assert seed_rows, "FAIL: no exec_log entry for seeded reflex"
   print(f"PASS: exec_log entry found — improved={seed_rows[-1].get('improved')}")
   ```

5. **Verify reflex_verdicts.json updated:**
   ```python
   import json, pathlib
   verdicts = json.loads((pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai\state\reflex_verdicts.json")).read_text())
   assert "metric:sword:Rule_Violations" in verdicts, "FAIL: verdict not persisted"
   print(f"PASS: verdict={verdicts['metric:sword:Rule_Violations']['verdict']}")
   ```

---

## Scenario 3 — Phantom reflex suppressed

**Goal:** Scout measures real value below threshold → `real:false` → REFUTED → engine skip broadcast → ledger `suppressed`.

1. Seed a phantom reflex (metric value already healthy in live source):
   ```python
   # Inject a reflex for a metric that is actually fine right now
   # e.g. if Rule_Violations = 0 in real session data
   payload["reflexes"].append({
       "id": "metric:sword:Rule_Violations_phantom_test",
       "tier": "HIGH",
       "status": "active",
       "pillar": "sword",
       "snapshot_value": 99,   # inflated stale snapshot
       "mitigation_command": "/policy-enforcement-audit",
       "_test_seed": True
   })
   ```

2. Run full cycle:
   ```
   claude --print -p "/sensei-cycle" --permission-mode acceptEdits --max-turns 40
   ```

3. **Expected:** Scout returns `real:false` → rival issues REFUTED → ledger row has `action_taken: "suppressed"` → `reflex_verdicts.json` has REFUTED entry → engine `_isEligible()` returns false → `auto_reflex_skipped` event emitted.

4. **Verify suppressed row:**
   ```python
   rows = [json.loads(l) for l in (pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai\state\SENSEI_LEDGER.jsonl")).read_text().splitlines() if l.strip()]
   phantom = [r for r in rows if r.get("reflex_id") == "metric:sword:Rule_Violations_phantom_test"]
   assert phantom and phantom[-1]["action_taken"] == "suppressed", f"FAIL: expected suppressed, got {phantom}"
   print("PASS: phantom reflex suppressed")
   ```

---

## Scenario 4 — Post-audit of prior code-modifying run

**Goal:** Next cycle's post-audit phase picks up the Scenario 2 exec_log entry (if `code_modifying: true`) and rival issues a post verdict.

1. After Scenario 2 completes, ensure `exec_log.jsonl` has the entry with `code_modifying: true`.

2. Run a second full cycle:
   ```
   claude --print -p "/sensei-cycle" --permission-mode acceptEdits --max-turns 40
   ```

3. **Expected:** sensei reads exec_log since prior watermark, finds `code_modifying: true` entry, spawns rival in post mode, appends `action_taken: "post_audit"` row to ledger.

4. **Verify post-audit row:**
   ```python
   rows = [json.loads(l) for l in (pathlib.Path(r"C:\Users\jemak\Desktop\Projects\Order Samurai\state\SENSEI_LEDGER.jsonl")).read_text().splitlines() if l.strip()]
   post_rows = [r for r in rows if r.get("action_taken") == "post_audit"]
   assert post_rows, "FAIL: no post_audit ledger rows"
   print(f"PASS: {len(post_rows)} post_audit rows found")
   ```

---

## Scenario 5 — Weekly window clears (cleanup)

**Goal:** After removing seed data, the Rule_Violations count returns to its real weekly value.

1. Remove test seeds from `wid_payload.json`:
   ```python
   payload["reflexes"] = [r for r in payload.get("reflexes", []) if not r.get("_test_seed")]
   (ROOT / "Governance\state\wid_payload.json").write_text(json.dumps(payload, indent=2))
   print("Removed test seeds")
   ```

2. Confirm no `_test_seed` reflexes in wid_payload.json:
   ```python
   assert not any(r.get("_test_seed") for r in payload.get("reflexes", [])), "FAIL: seeds not removed"
   print("PASS: wid_payload.json clean")
   ```

3. Run a final dry-run cycle and confirm seeded reflex_ids are absent from output.

---

## Pass / Fail summary

| Scenario | Check | Pass Condition |
|----------|-------|----------------|
| 1 — Dry-run | Ledger row written, no POST | CONFIRMED ledger row; reflex_verdicts.json unchanged |
| 2 — Full cycle | Engine fires, exec_log updated | exec_log entry present; reflex_verdicts.json has CONFIRMED |
| 3 — Phantom | Scout real:false → suppressed | Ledger `action_taken: suppressed`; REFUTED in verdicts |
| 4 — Post-audit | Second cycle audits prior run | Ledger `action_taken: post_audit` row present |
| 5 — Cleanup | Seeds removed, payloads clean | No _test_seed in wid_payload.json |

All 5 scenarios pass → **sensei-cycle E2E PASSED**.
