# Order Samurai — Public Metric Honesty Table

_Version 1.0.0 · Published: 2026-07-13_

Order Samurai's core value proposition is **provenance-transparent governance**. We do not present unvalidated estimates or synthetic scores as facts. Every metric displayed on the CLI or Dashboard carries an explicit status:

- **`MEASURED`**: Derived directly from system execution logs, atomic ledger files, or real-time security hook events.
- **`SIMULATED`**: Benchmark placeholder displayed during the calibration phase (until 20 sample measurements or 4 weeks of continuous telemetry accumulate).

---

## Metric Honesty Matrix

| Pillar | Metric | Telemetry Status (v1) | Data Source File | Calibration Trigger | Auditability |
|---|---|---|---|---|---|
| 🗡️ **SWORD** | **Kill Chains Disrupted** | **`MEASURED`** (Day 0) | `state/kill_chain_events.jsonl` | Immediately calibrated (count-based) | Append-only event log with timestamp, hook source, and confidence score |
| 🏹 **BOW** | **Estimated Agent Time Saved** | **`MEASURED`** (timed) / **`SIMULATED`** (placeholder) | `state/DOJO_STATE.json` (`started_at`, `completed_at`) | 20 completed backlog items with wall-clock timing | Task duration × task-kind coefficient from `state/calibration_coefficients.json` |
| 🎨 **BRUSH** | **Estimated Cost Savings** | **`MEASURED`** (actual spend) + **`SIMULATED`** (routing delta) | `state/budget_ledger.json` & `autonomic_events.jsonl` | 14 daily ledger entries for actual spend; 20 routing events for delta | Direct USD delta vs prior week + Anthropic list price math |
| 🎭 **ARTS** | **Estimated Human Time Saved** | **`SIMULATED`** (calibrating) | `state/DOJO_STATE.json` & `autonomic_events.jsonl` | 20 documentation/craft alignment signals | Sum of Vibe alignment delta, doc parity latency reduction, and skill promotions |

---

## Runtime Coverage & Telemetry Bounds

### Claude Code (Primary v1 Runtime)
- **Hooks Active**: `PreToolUse` (prompt injection defense), `PostToolUse` (secret scrubber & credential exfiltration defense).
- **Telemetry Captured**: Tool invocation types, parameters, execution status, redacted secret counts, and budget ledger records.
- **Fail-Closed Gate Guarantee**: Security hooks execute synchronously (`async: false`). Failures block execution when `BUSHIDO_FAIL_OPEN=false`.

### Other Agent Runtimes (Codex / Custom Agents)
- **Status in v1**: Adapter interface supported via `runtime_paths.py` & standard JSONL schema. Metrics render `SIMULATED` until native adapter hooks are initialized.

---

## Calibration Rules & Transition Invariants

1. **Threshold**: A metric transitions from `SIMULATED` to `MEASURED` automatically when **20 distinct verified telemetry events** or **4 continuous weeks of log data** are recorded.
2. **Badge Invariant**: Any UI view or API response MUST include `"calibrated": false` and display the visual benchmark badge whenever benchmark placeholders are in use. Removing or hiding the calibration badge is strictly forbidden.
3. **Stale Data Guard**: If 0 real events are recorded during an active week, the reducer sets `"data_gap": true` and displays a data warning rather than reporting false zero savings.
