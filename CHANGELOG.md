# Changelog

All notable changes to **Order Samurai** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `samurai install` and `samurai doctor` now name other coding agents found on the machine that Order Samurai cannot protect yet (Windsurf, Goose, Cline, OpenCode, Aider, Continue; Cursor and Gemini CLI were in this list until they gained their own hook support, below), so a machine with Claude Code plus another agent is not mistaken for fully covered. Detection only reads PATH, folders in your home directory and `/Applications`; exit codes are unchanged.
- Continue is named as not protected even though it reads `~/.claude/settings.json`: its CLI loads hook settings but never runs `PreToolUse`/`PostToolUse` hooks on tool calls, so the Claude Code hooks do not protect it.
- `samurai install` now also installs the prompt-injection guard for **Codex**. It detects which agent harnesses are installed (Claude Code and Codex) and registers the prompt-injection guard for each, printing one result line per harness. Choose harnesses with `--harness claude,codex` or `SAMURAI_HARNESS`; the choice is remembered.
- On Codex the guard scans the `Bash` and `apply_patch` calls Codex sends to `PreToolUse` hooks; for `apply_patch` it scans only the lines a patch adds and its file names. It does not cover `write_stdin` to an already-running shell, MCP tools, other tool routes, or anything while Codex hooks are disabled. Codex runs the hook only after you approve it in `/hooks`; Order Samurai never approves it for you. The secret scrubber is not mirrored to Codex.

- `samurai install` now also installs the prompt-injection guard for **Cursor**: `--harness cursor` (or `SAMURAI_HARNESS`), auto-detected from `cursor`/`cursor-agent` on PATH or `Cursor.app`; a `~/.cursor` folder alone is skipped like a Codex config folder. It adds `beforeShellExecution`, `beforeMCPExecution` and `preToolUse` (`Write`) entries to `~/.cursor/hooks.json`, each with `failClosed` so a guard failure blocks. `samurai doctor` checks the entries and probes the guard with Cursor payloads; like Codex, it does not claim Cursor enforcement. Install keeps your other Cursor hooks and backs the file up; uninstall removes only Order Samurai's entries.
- The guard reads Cursor payloads (the top-level `command` of `beforeShellExecution`, and the JSON-string `tool_input` of `beforeMCPExecution`, scanned both raw and with its JSON escapes decoded) and answers in Cursor's format: exit 2 plus `{"permission":"deny",...}` to block, `{"permission":"allow"}` otherwise, and exit 2 on its own errors. Claude Code and Codex output is unchanged.
- Cursor coverage limits: no file reads, no Tab completions, user-level hooks only (enterprise, team and project hook files are untouched), and the `cursor-agent` CLI is untested (reported to wire fewer hook events). Order Samurai does not write a scrubber hook for Cursor; if Claude Code is also installed, Cursor's third-party-hooks import runs the Claude hooks too (see the onboarding guide). Payload fixtures are documentation-derived; no live Cursor capture yet.

- `samurai install` now also installs the prompt-injection guard for **Gemini CLI**: `--harness gemini` (or `SAMURAI_HARNESS`), auto-detected from `gemini` on PATH; a `~/.gemini` folder alone is skipped like a Codex or Cursor config folder. It adds a `BeforeTool` hook (matcher `^(run_shell_command|write_file|replace|mcp_.+|discovered_tool_.+)$`, command `python3 '<path>' --gemini`, `timeout` `10000` because Gemini's unit is milliseconds) to the `hooks` section of `~/.gemini/settings.json`. Every other setting and hook in that file is kept (including the older `hooks.enabled`/`disabled`/`notifications` keys), the file is backed up and keeps its permissions, and uninstall removes only Order Samurai's entry. `samurai doctor` checks the entry, fails if your user `settings.json` switches the hook off through `hooksConfig`, and probes the guard with Gemini payloads; like Codex and Cursor, it does not claim Gemini enforcement.
- The guard reads Gemini `BeforeTool` payloads (every string in `tool_input` at any depth, plus JSON carried inside argument strings) and answers in Gemini's format: pure-JSON stdout, `{"decision":"deny",...}` with exit 2 to block, `{}` otherwise (never an explicit allow). With the `--gemini` flag it also exits 2 with a deny when its input cannot be parsed or on its own errors, because Gemini proceeds when a hook prints nothing. Claude Code, Codex and Cursor entries carry no flag and are unchanged.
- Gemini CLI coverage limits: **Gemini loads no hooks at all in a folder it does not trust (folder trust is on by default), so the guard does not run there; trust the folders you work in and run the manual check inside them.** Doctor notes this and reads only your user `settings.json`: a trusted project's `.gemini/settings.json` or system settings can still switch the hook off. No `read_file`, `web_fetch` or other built-in tools; user-level settings only (project, system and extension hooks are untouched); a `settings.json` with comments or an install path containing `$` is refused; a JSON-in-string argument that fails to decode is scanned raw only. Order Samurai writes no scrubber hook for Gemini. Payload fixtures are documentation-derived; no live Gemini CLI capture yet.

### Changed

- Gemini CLI is no longer listed as an agent Order Samurai cannot protect.
- The web installer (`install.sh`) now re-downloads the zip and its `.sha256` up to three times when they do not match, bypassing the CDN cache, so an install right after a release no longer fails because the cache paired an old zip with a new checksum. A zip that never matches is still refused with nothing extracted. `OS_CORE_BASE_URL` overrides the download location (used by tests).
- Cursor is no longer listed as an agent Order Samurai cannot protect.
- `samurai install` now exits 1 when it finds no supported harness (none of Claude Code, Codex, Cursor or Gemini CLI) and so installs nothing. It used to exit 0, so the `curl | bash` installers reported success on a machine with no protection; they now stop at that message, which names the command to re-run once a harness is installed.
- Install records exactly what it wrote in `~/.samurai/install.json` and treats only an exact match as its own. A hook that merely mentions an Order Samurai script is never modified or removed.
- Re-installing leaves an unchanged hook untouched and updates a changed one in place, so Codex asks you to re-approve that hook only. `samurai uninstall` will not shift later Codex hooks (which would make Codex ask to re-approve them) unless you pass `--force`.
- Config writes are atomic, keep the file's permissions, refuse symlinked or special files and abort if another program changed the file mid-write.
- `samurai doctor` no longer runs commands read from your config. It runs the packaged guard and scrubber directly and reports a changed registration as "registration mismatch". For Codex it never claims enforcement, and it shows what it can read about your `/hooks` approval.
- The guard's local-model check is bounded to 3.5 seconds in total, and its endpoint can be set with `PIG_LMSTUDIO_URL` (this machine only: a non-local URL is ignored, so tool input never leaves it).

### Fixed

- Config files `samurai` rewrites (`~/.claude/settings.json`, Codex, Cursor and Gemini configs, `install.json`) keep non-ASCII text as written instead of turning it into `\u` escapes; the meaning of the file is unchanged.
- `samurai uninstall` kept reporting success and deleted `~/.samurai` when it could not deregister a hook. It now keeps the state and exits non-zero.
- Install no longer replaces an unreadable `~/.claude/settings.json` with a new file.
- `bin/install.sh` no longer fails on Homebrew and other PEP 668 "externally managed" Pythons, which refuse `pip install --user`: it installs the report dependencies into a private virtualenv (`~/.samurai/venv`) so the first cost report runs. Other Pythons keep the `--user` install, and an interpreter that is already a virtualenv is used directly. Hooks stay on the system `python3`.
- `samurai install` no longer adds its two hooks to `~/.claude/settings.json` on a machine whose Claude hook registry (`~/.claude/scripts/hook_registry.py`) registers both hooks. That registry's dispatcher already runs the guard and the scrubber, so the direct entries ran each hook twice. `~/.samurai/settings.json` is still written, and machines without such a registry are unchanged. `samurai doctor` passes its hook-registration check when the registry registers both hooks (its hook-execution check then runs the bundled scripts), and fails it when the hooks are registered both ways, since each would run twice.

## [2.1.3] - 2026-10-06

### Fixed

- Real-time secret scrubbing now runs on a standard install. The scrubber hook depended on a helper module that a standard install does not include, so it exited with an error on every tool call and scrubbed nothing. It now uses the secret patterns shipped with Order Samurai.
- `samurai doctor` now runs each registered hook on a harmless tool call and fails if one errors or hangs. Previously it only checked that the hook files existed, so it reported every check as passing while the scrubber was failing. Doctor now runs 6 checks.

## [2.1.2] - 2026-10-04

### Fixed

- Added previous and next controls with a visible position for categorized reflex stacks.
- Kept the selected card stable when other alerts disappear; selected-card actions retain their targets.
- Fixed category stack width on narrow screens.

## [2.1.1] - 2026-10-04

### Fixed
- A license key disabled in Gumroad now revokes Pro on the next refund-window re-check, the same as a refund.

## [2.1.0] - 2026-10-04

### Added
- One-command Pro install: after installing, `install.sh` asks for the license key from the Gumroad receipt (input hidden) and activates it, or keeps the Free tier when you press Enter. The key never goes on a command line or into shell history.

### Fixed
- Revoke Pro after a refund: an activated license is re-checked online at most once per 24 hours during the refund window (purchase + 21 days). Only a confirmed refund, chargeback or lost dispute revokes. A network failure never does, so offline activation keeps working.
- `samurai license` masks the buyer's email (`b****@example.com`) as well as the key.
- Installer: hooks are registered at `bin/`, where the scripts ship; hook script paths are quoted so installs under folders with spaces work; installer child processes no longer read stdin.
- The secret scanner applies directory exclusions only below the scan root. A checkout under a directory with an excluded name was previously skipped entirely.
- The HITL alert digest ages proposed backlog items by creation time.
- The demo dashboard no longer renders blank when repair history is empty.

### Security
- Lockfile bump clears two critical npm advisories (`shell-quote` 1.9.0, `concurrently` 9.2.4).

## [2.0.1] - 2026-10-02

### Fixed
- Label static demo reflexes as sample data, not live telemetry.
- Use recorded Craft Improvements for the Arts headline instead of conflating
  deliverables with estimated human hours saved.
- Document complete dependency/hook setup, current support channels, online
  Gumroad activation and the limits of refund revocation for offline entitlements.
- Align the synthetic demo and customer ZIP with the reviewed public source.

## [2.0.0] - 2026-09-23

### Breaking — broad autonomous repair retired

- Retire broad unattended LLM-generated repair and autonomous patch application.
  `REFLEX_AUTO_APPLY` no longer enables auto-apply on either tier; remove this
  legacy setting. Staged repairs require explicit human review and approval.
- Retain diagnosis, validation evidence, existing bounded deterministic maintenance,
  and explicitly approved backlog work. Passing validation alone does not authorize
  a repair.
- Replace autonomous repair promises in the dashboard, onboarding, and commercial
  feature descriptions with diagnostics and staged repair review. Pricing and
  license terms remain unchanged.
- Align the package, CLI, installer, and JavaScript version mirrors at `2.0.0`.

## [1.0.1] - 2026-08-10

### Fixed — hook wiring (critical: v1.0.0 protected nothing)

- **`samurai install` now registers into `~/.claude/settings.json`**, the config
  Claude Code actually loads. v1.0.0 wrote to `~/.claude/hooks/settings.json`,
  a path Claude Code never reads, so the prompt-injection guard and secret
  scrubber never fired on any install.
- **Hook entries now use Claude Code's schema** — `{"matcher": ..., "hooks":
  [{"type": "command", ...}]}`. v1.0.0 emitted `{"name", "command", "async"}`,
  which Claude Code does not parse, so fixing the path alone was not sufficient.
- **`samurai doctor` no longer reports a false green.** Registration is asserted
  only against `~/.claude/settings.json` in the real schema; v1.0.0 accepted its
  own `~/.samurai/settings.json` as proof and printed 5/5 PASS while unprotected.
- **`samurai uninstall` no longer risks destroying your Claude Code config.** It
  deregistered by name, then blind-restored the newest `settings.json.bak.*` —
  but both settings files share that basename, so it could drop Order Samurai's
  file over your real config. Uninstall is now surgical (your own hooks are
  preserved) and backups are labelled per target.
- **`samurai uninstall` preserves a paid Pro license.** It previously removed
  `~/.samurai` wholesale, silently deleting `license.json`; the license is now
  copied to `~/order-samurai-license-backup.json` first.
- Settings writes are atomic (temp file + `os.replace`).
- Regression coverage added in `tests/test_hook_wiring.py`. The pre-existing
  `tests/test_samurai_installer.py` asserted the broken path and schema, which is
  why the defect shipped; it now asserts the real contract.

### Upgrading from 1.0.0

Re-run `samurai install` to wire the guard correctly, then `samurai doctor` —
it will now fail honestly if the guard is not registered where Claude Code
loads it. `samurai uninstall` cleans up the stale v1.0.0 hook file.

## [1.0.0] - 2026-07-19

### Fixed — metric integrity (2026-07-19 sync from upstream)
- **No blended pillar scores anywhere**: pillar status is a worst-tier rollup
  (passing/graded counts) — a hard FAIL can never be averaged away. Radar axes
  and per-project scores are pass rates.
- **Fire-time remediation efficacy**: the engine records each metric's live value
  before and after every autonomous run; the efficacy panel counts every attempt
  (including no_change/error/timeout) instead of rendering silence.
- **Alarm quality**: metrics marked non-remediable no longer generate remediation
  reflex cards; correlation gates normalize per-session metrics before comparing
  thresholds (a raw-count bug fired a cost reflex whose own gate failed).
- **Honest thresholds**: bimodal-by-workload metrics (e.g. Avg_Session_Turns)
  opt out of percentile calibration; simulated metrics collapse by default in the
  dashboard until their emitter produces real data.
- **Ship the payload schema**: a gitignore pattern for generated payloads also
  swallowed `schema/wid_payload.schema.json` — fresh clones failed 13 schema
  tests and API startup validation. Found by the release clone-and-run test.
- Dashboard `ThresholdSparkline` had an undefined gradient id that only surfaced
  on cache-free builds (stale tsbuildinfo masked it in dev).

### Security
- **Autonomous patch-apply is OFF by default** (`REFLEX_AUTO_APPLY`, opt in with `=true`): a
  code-modifying remediation that passes the maker-checker audit + pytest gate is now saved to
  `state/pending_remediation_*.patch` for human review instead of being applied to the live
  repo, so a fresh clone never rewrites a working tree unattended. The audit + pytest gate run
  identically either way.
- **Daemon validate-command hardening**: `bin/ronin-daemon.sh` reads `DOJO_VALIDATE_CMD` from
  the environment inside Python rather than interpolating it into the source string, closing a
  code-injection path where a quote in the value could break out of the literal.
- **Removed dead secondary-model fallback**: the `runFallback` branch that spawned an absent
  `execute_remediation_gemini.py` is gone from the API server and reflex engine (Claude-only
  build). A CLI quota limit is now surfaced directly instead of erroring on a missing script.
- **API server binds to loopback by default** (`127.0.0.1`, override with `DOJO_BIND_HOST`):
  the dashboard API can spawn an auto-editing agent, so it must never be reachable off-host.
  Previously it bound to all interfaces (`0.0.0.0`).
- **WebSocket `/ws` now enforces an `Origin` allow-list**: the agent-spawning `{type:'exec'}`
  channel rejects connections from any origin outside the dashboard allow-list (browsers apply
  no CORS to WebSocket upgrades, so this is its only cross-origin gate).
- **State-mutating REST routes are localhost + same-origin gated** (`unstick`, `unstick-all`,
  `cancel`, `ronin/toggle`, `dojo/run`), mirroring the existing `/api/reflex/verdicts` gate —
  closes a CSRF / off-host path to disabling loop-breaker safety and forcing remediation runs.
- **Remediation patch filenames fully sanitized** so a reflex id can never escape `state/`.

### Fixed
- **`bin/emit_event.py` standalone resolution**: it hardcoded a developer-machine directory
  layout and, on a fresh clone, silently wrote telemetry to a per-machine `~/Desktop/...` path.
  It now resolves the repo root from its own location like the other `bin/` scripts.

### Added
- **Reflex fire-path verify-gate (`REFLEX_VERIFY_GATE`, default on)**: before spawning an
  expensive code-modifying remediation skill for a batch metric, re-measures the breach live
  (`bin/remeasure_gate.py`) and suppresses the spawn if the metric already recovered — closing
  a fail-open gap where a stale/phantom breach spent a full skill run. Fail-open on any gate error.
- **Overnight batch-defer routing (`REFLEX_BATCH_WINDOW`, default off)**: holds non-urgent,
  code-modifying remediations for a configured overnight window (verify real-time, improve
  overnight); the metric re-fires via the normal poll when the window opens.

### Added
- **14 ATT&CK Kill-Chain Security Taxonomy**: Full security detection and interception layer mapping agent execution vulnerabilities.
- **Prompt Injection Guard (Chain 13)**: Real-time PreToolUse hook intercepting role manipulation and system prompt override attacks.
- **Exfiltration & Secret Scrubber (Chain 14)**: PostToolUse hook scanning stdout, UNC paths, connection strings, and RFC1918 internal IPs.
- **4 Business Pillar Aggregates**:
  - `Kill_Chains_Disrupted` (SWORD) - Resilience count of intercepted attack vectors.
  - `Estimated_Agent_Time_Saved` (BOW) - Operation efficiency metrics from verified runtimes.
  - `Estimated_Cost_Savings` (BRUSH) - Real token expenditure & model routing delta.
  - `Estimated_Human_Time_Saved` (ARTS) - Craft productivity & documentation parity.
- **Honesty Invariant**: Explicit `MEASURED` vs `SIMULATED` calibration badge rendering across all UI and CLI surfaces.
- **One-Command Installer CLI (`bin/samurai`)**:
  - `samurai install`: Safe merge with write-through settings backup (`~/.claude/hooks/settings.json`).
  - `samurai doctor`: Complete environment, gate posture, and secret scrubber verification.
  - `samurai uninstall`: Zero-residue restoration of original developer settings.
- **Web Dashboard & Product Landing Page**: Integrated UI surface with interactive metrics, pricing calculator, and self-serve checkout interface.
