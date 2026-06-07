---
title: "Order Samurai TUI: Per-Pillar Ronin Mode Toggle Pattern"
date: 2026-06-06
category: docs/solutions/best-practices/
module: Order Samurai TUI
problem_type: best_practice
component: tooling
severity: medium
applies_when:
  - Adding interactive toggle state to Ink TUI dashboard rows
  - Wiring keyboard shortcuts that map to a fixed set of named items
  - Reading and writing JSON state files from within a React/Ink component
tags: [tui, ink, ronin, dojo, keyboard-shortcuts, dojo-state, react-hooks, auto-remediation]
---

# Order Samurai TUI: Per-Pillar Ronin Mode Toggle Pattern

## Context

The Order Samurai TUI (`tui.ts`) needed a toggle button on each pillar summary row that
(1) reflected `ronin_mode` from `state/DOJO_STATE.json`, (2) could be activated from the
dashboard without navigating to the detail view, and (3) automatically triggered remediation
when a pillar's `live_current` fell below `live_baseline`.

Two non-obvious discoveries during implementation:

1. **`bin/ronin-pillar` was missing on main** — it existed only on the overnight branch
   (`7daeadc`, never cherry-picked), but `tui.ts` already referenced it in `runRoninForPillar()`.
   The script was recoverable via `git show 7daeadc:bin/ronin-pillar`.

2. **Two separate state concepts need to stay distinct** — `roninStatus` (runtime:
   idle/running/done/error) vs `ronin_mode` in DOJO_STATE.json (persistent: "ronin"/"dormant").
   Mixing them into one field breaks the button display once the agent finishes.

## Guidance

### Keyboard shortcut lookup table over parallel else-if blocks

When adding multiple keyboard shortcuts that all do the same thing to different items,
use a record lookup instead of repeating the handler body:

```typescript
// ❌ Before — 4 identical else-if blocks (16 lines, hard to extend)
} else if (input === "!") {
  toggleRoninMode("bow");
  void refresh("dashboard", "Bow ronin mode toggled");
} else if (input === "@") {
  toggleRoninMode("sword");
  void refresh("dashboard", "Sword ronin mode toggled");
} ...

// ✅ After — single lookup block (5 lines, trivially extensible)
const PILLAR_TOGGLE_KEYS: Record<string, PillarSlug> = {
  "!": "bow", "@": "sword", "#": "brush", "$": "arts",
};

} else if (PILLAR_TOGGLE_KEYS[input]) {
  const togglePillar = PILLAR_TOGGLE_KEYS[input];
  toggleRoninMode(togglePillar);
  void refresh("dashboard", `${PILLAR_META[togglePillar].name} ronin mode toggled`);
}
```

Keep a parallel display map for rendering:
```typescript
const TOGGLE_SHORTCUTS: Record<PillarSlug, string> = {
  bow: "!", sword: "@", brush: "#", arts: "$",
};
// Used in summary row: `[${TOGGLE_SHORTCUTS[slug]}][${meta.shortcut}]`
```

### Synchronous JSON read-write for DOJO_STATE.json

The TUI already uses `readFileSync` for loading state. The toggle writer follows the same pattern:

```typescript
import { readFileSync, writeFileSync } from "node:fs";

function toggleRoninMode(pillar: PillarSlug): void {
  const stateFile = path.join(process.cwd(), "state", "DOJO_STATE.json");
  try {
    const current = JSON.parse(readFileSync(stateFile, "utf8")) as DojoState;
    const existing = current.pillars[pillar].ronin_mode;
    current.pillars[pillar].ronin_mode = existing === "ronin" ? "dormant" : "ronin";
    writeFileSync(stateFile, JSON.stringify(current, null, 2), "utf8");
  } catch { /* ignore — stale state is fine */ }
}
```

The caller always follows the write with `void refresh(...)` to reload the in-memory state.

### Mode-aware button rendering

Two separate display layers — persistent mode from dojoState, runtime status from roninStatus:

```typescript
const isEnabled = p.ronin_mode === "ronin";  // from dojoState (persistent)
const rStatus = roninStatus[slug] ?? "idle"; // from AppState (runtime)

// Runtime active states override the mode display
const rLabel =
  rStatus === "running" ? "[~~ active ]" :
  rStatus === "done"    ? "[OK done   ]" :
  rStatus === "error"   ? "[!! error  ]" :
  isEnabled             ? "[◉ Ronin ON]" :
                          "[○ Ronin  ]";
```

### Auto-remediation via useEffect on data

A `useEffect` that watches `state.data` fires after every refresh and auto-triggers ronin
for any pillar that is enabled AND below baseline AND not already running:

```typescript
useEffect(() => {
  if (!state.data?.dojoState) return;
  const ds = state.data.dojoState;
  for (const slug of PILLAR_SLUGS) {
    const p = ds.pillars[slug];
    if (p.ronin_mode !== "ronin") continue;
    if (p.live_current === null) continue;
    if (p.live_current >= p.live_baseline) continue;
    if ((state.roninStatus[slug] ?? "idle") === "running") continue;
    runRoninForPillar(slug, (status, msg) => {
      setState((s) => ({
        ...s,
        roninStatus: { ...s.roninStatus, [slug]: status },
        roninMsg: { ...s.roninMsg, [slug]: msg },
        notice: `Auto-remediation: ${slug} below baseline`,
      }));
    });
  }
}, [state.data]);
```

Note: `live_current` is `null` in the seed state — the guard prevents spurious fires on a
fresh repo before any dojo cycle has run.

### Recovering a committed script missing from main

When a script exists in git history but not on the working tree:

```bash
git log --oneline --all    # find the commit
git show <commit-hash>:<path>  # read the content
# then Write the file manually
```

Check `.gitignore` first — `.mex/` is gitignored by design, so tui.ts changes are never committed.
Only files outside gitignored directories (like `bin/ronin-pillar`) need explicit committing.

## Why This Matters

- **Lookup table vs else-if blocks**: Every new pillar would require adding a new else-if in two
  places. The lookup table is O(1) and extensible in one location.
- **Keeping runtime vs persistent state separate**: If `roninStatus` and `ronin_mode` are conflated,
  pressing the toggle while an agent is running clears the running indicator, or vice versa.
- **Auto-remediation via useEffect**: Avoids polling — the effect fires exactly when new data arrives,
  which is the right trigger. No setInterval needed for the basic case.

## When to Apply

- Adding any keyboard shortcut that maps keys to items in a fixed set (pillars, views, etc.)
- Adding JSON state mutations to the TUI (any field in DOJO_STATE.json)
- Implementing "enable and watch" patterns in the dashboard (autonomic behavior per pillar/module)
- Recovering scripts that were committed on a feature branch but never merged to main

## Examples

**Summary row output after toggle ON:**
```
Bow     0/15  ░░░░░░░░░░  [◉ Ronin ON]  [!][1]
Sword   0/12  ░░░░░░░░░░  [○ Ronin  ]  [@][2]
```

**Auto-remediation notice (shown in dashboard header):**
```
Auto-remediation: bow below baseline
```

**Footer help text pattern:**
```
↑/↓ choose · enter run · r refresh · l log · 1-4 pillars · !/@/#/$ toggle ronin · esc back · q quit
```

## Related

- `bin/ronin-pillar` — single-pillar SENSEI launcher (commit `20e8b33`)
- `state/DOJO_STATE.json` — `pillars[slug].ronin_mode` field ("ronin" | "dormant")
- `prompts/dojo_cycle.md` — STEP C reads `ronin_mode` to filter backlog items
- `Research/autonomous_ronins.md` — autonomic properties per pillar
