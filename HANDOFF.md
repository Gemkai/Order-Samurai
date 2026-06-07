## Handoff — 2026-06-06 Per-Pillar Ronin Mode Toggle

### Files changed

| File | Reason |
|------|--------|
| `bin/ronin-pillar` | Created — single-pillar SENSEI launcher missing from main (existed only on overnight branch commit `7daeadc`, never cherry-picked). The TUI's `runRoninForPillar()` already expected this path. |
| `.mex/src/tui.ts` | Modified (not git-tracked — `.mex/` is gitignored): added `toggleRoninMode()`, `TOGGLE_SHORTCUTS`, `PILLAR_TOGGLE_KEYS`, updated `Summary` pillar row rendering, added keyboard handlers, added auto-remediation `useEffect`, updated footer help text. |

### What was built

**Per-pillar ronin mode toggle button on the dashboard summary rows.**

Each pillar row now shows a mode-aware button — `[◉ Ronin ON]` (green) or `[○ Ronin  ]` (dim) — read from `state/DOJO_STATE.json`. Pressing `Shift+1/2/3/4` (`!/@/#/$`) on the dashboard toggles that pillar's `ronin_mode` field between `"ronin"` and `"dormant"` and refreshes immediately.

Auto-remediation: a `useEffect` on `state.data` fires after every data load and calls `runRoninForPillar()` for any pillar where `ronin_mode === "ronin"` AND `live_current < live_baseline` AND no agent is already running. This implements autonomous guardian behavior (RONIN_SPEC Tier 2) for each pillar independently.

### Tests run

```
cd .mex && npx tsc --noEmit   → PASS (TypeScript clean, twice — before and after /simplify)
bash bin/ronin-pillar badpillar → exit 1, "Unknown pillar: badpillar" (arg validation OK)
```

### Open risks

- `bin/ronin-pillar` actually launches `claude -p …` which requires the `claude` CLI to be in PATH and authenticated. The TUI shows `[!! error ]` if the spawn fails — user will see it.
- The auto-remediation `useEffect` reads `state.roninStatus` at time of data load. If a pillar transitions to running mid-refresh, there's a window where it could double-trigger. Existing `if (currentStatus === "running") continue` guard mitigates this but race is not impossible in rapid refresh scenarios.
- `live_current` is `null` for all pillars in the current seed state — auto-remediation only fires when `live_current` is a real number, so no spurious fires on a fresh repo.

### Security surface

No new endpoints, no auth changes. The only user-facing input path is the keyboard `input` character in `useInput`, which is constrained by `PILLAR_TOGGLE_KEYS` record lookup — only `!/@/#/$` resolve to a valid `PillarSlug`; all other characters are no-ops. No raw input reaches `spawn()`.

### Rollback plan

**git revert** — `bin/ronin-pillar` is the only committed artifact. Reverting `20e8b33` removes it:

```bash
git revert 20e8b33 --no-edit
```

The `tui.ts` changes are outside git (`.mex/` gitignored). To revert `tui.ts` manually, restore the 5 changed sections:
1. `import { readFileSync }` (remove `writeFileSync`)
2. Remove `toggleRoninMode()`, `TOGGLE_SHORTCUTS`, `PILLAR_TOGGLE_KEYS`
3. Restore original `rLabel`/`rColor` ternaries (no `isEnabled` branch)
4. Remove `!/@/#/$` keyboard handler block, restore 4-line original
5. Remove auto-remediation `useEffect`
6. Restore original footer string (drop `!/@/#/$` hint)

### Expected Antigravity tasks

- Integration test: launch `mex` TUI, verify toggle buttons render and respond
- Verify auto-remediation fires correctly when a pillar drops below baseline (requires real `live_current` data flowing)
- Consider adding a 60-second auto-refresh interval when any pillar has `ronin_mode === "ronin"` (currently auto-remediation only fires on manual refresh)
