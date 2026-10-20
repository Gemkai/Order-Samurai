# Changelog

All notable changes to **Order Samurai** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.4.1] - 2026-10-20

### Fixed

- A fresh install no longer shows the bundled demo metrics (for example "Claude Code 144 sessions" and a "STALE DATA" banner) as if they were the buyer's own. `bin/install.sh` moves the demo payload aside in an installed copy (to `dashboard-ui/public/wid_payload.demo.json`) before anything can fail. It then runs `refresh_dashboard.py` with the same Python (2-minute limit, non-fatal), so the dashboard opens on this machine's real, initially empty, data. A developer checkout (a `.git` directory or file) keeps the tracked demo payload. Reports history is kept.
- `npm run sync` ran `python`, which stock macOS does not have. It now uses `~/.samurai/venv/bin/python` when that venv works, otherwise `python3` (`python` on Windows), called with an argument list.

## [2.4.0] - 2026-10-20

### Added

- `samurai audit` and the installer's first report read Codex session logs (`$CODEX_HOME/sessions`, default `~/.codex/sessions`). Per harness they report sessions scanned, tool calls and token totals (input, cached, output) as recorded in the logs. Codex logs record tokens but not prices, so Codex cost is shown as unavailable. No estimate is made, and Codex sessions never appear in the dollar totals or the spend-spike list. `samurai audit` and `bin/first_blood.py` accept `--logs-dir` (Claude Code transcripts) and `--codex-dir`.
- `samurai restart-hint` prints the restart step for each installed harness. For Codex it says to restart Codex and approve the Order Samurai hooks in `/hooks`. `samurai install` prints it at the end.
- `samurai version [--json]`. With `--json` it prints the installed and latest versions (the same daily-cached, offline-silent check as doctor's notice).
- The dashboard gets its own version status from `GET /api/version`. The Vite dev and preview servers answer it even when the API is not running, and so does the API server. The update banner therefore appears without a manual `npm run sync`. Behaviour is the same for Free and Pro.

### Fixed

- Install, doctor, audit and first-report messages name the harnesses that are actually detected or installed (Claude Code, Codex, Cursor, Gemini CLI, or any mix). A Codex-only machine is no longer told to "Run a Claude Code session first", and harnesses that are not present are no longer listed as skipped.
- The Claude Code price table is no longer applied to models from other providers.

### Changed

- Removed the remaining Touch ID claims from `docs/MARKETING_STRATEGY.md`, `docs/MONETIZATION_STRATEGY.md` and `docs/SEO_CONTENT_STRATEGY.md`. Order Samurai has no Touch ID code.

## [2.3.0] - 2026-10-20

### Added

- `samurai audit`: a full audit from a web install (doctor, agent spend scan and weekly telemetry reports), written as `audit__<UTC>.md` plus `index.json` to the install's `dashboard-ui/public/reports` (override with `SAMURAI_REPORTS_DIR`). Exit codes: 0 clean, 1 findings, 2 cannot write. After doctor passes, `install.sh` asks `Run a full audit now? Results appear in the Reports tab. [Y/n]`. It reads only `/dev/tty`, so `curl | bash` still works, and skips the question under `SAMURAI_NO_PROMPT=1` or with no terminal. Audit findings never fail the install. (#86)
- The Reports tab reads your real reports from `reports/index.json`, newest first. Free shows the newest report and locks older ones; Pro shows all. A locked report is never fetched. With no reports the tab says so and names `samurai audit`. `?demo` keeps the hosted samples. (#86)
- Update-available notice, the same on Free and Pro. `samurai doctor` (and so the installer's closing summary) prints one line when a newer release is published, with the update command `curl -fsSL https://www.ordersamurai.ai/install.sh | bash`. The dashboard shows a banner with a button that copies that command. The check fetches the public `dist/build-record.json` at most once a day, caches it in `~/.samurai/update_check.json`, times out after 3 seconds and is silent when offline; it never changes doctor's result. Opt out with `SAMURAI_NO_UPDATE_CHECK=1`. Rerunning the installer keeps `~/.samurai/license.json`, `~/.samurai/settings.json` and your `meditation.env` / `dojo.env` (now covered by a test). (#85)

### Fixed

- `bin/first_blood.py` no longer fails with `ModuleNotFoundError` from a web install. Weekly reports are written inside the install, where the Reports tab serves them, instead of outside it. Report dependencies run under `~/.samurai/venv` when the installer created one. (#86)
- The v2.2.1 zip shipped vendor sample reports and an old-format `index.json` that every buyer would have seen as their own history. They are removed, and the installer deletes them on upgrade. (#86)

### Changed

- `docs/ONBOARDING.md` now says correctly that the curl installer registers hooks and runs doctor, and documents the audit prompt and the Reports tab. (#86)
- Removed the "Touch ID hardware authorization" claim from the README and the dashboard's landing page. Order Samurai has no Touch ID or LocalAuthentication code.

## [2.2.1] - 2026-10-14

### Security

- `install.sh` (and the website copy, `dashboard-ui/public/install.sh`): when the installer is piped in (`curl … | bash` or `bash < install.sh`) from a folder that contains `bin/samurai`, macOS's built-in bash 3.2 ran that local file instead of downloading the release, skipping the sha256 check. The local-installer path now runs only when the script was started from a real file on disk. Homebrew bash 5.x was not affected. (#79)
- `samurai license` and the shell Pro gate (`bin/lib_pro_gate.sh`) no longer load an `agentica_core` package placed in the folder above the install. They load the bundled copy first, then fall back to the development layout. The gate's Python runs with `-I`, so a module planted in the current folder or on `PYTHONPATH` (for example a `json.py`) can no longer be imported and grant Pro. (#79, #81)
- `samurai uninstall` writes its license backup with owner-only permissions (0600) from the moment the file is created, and replaces it atomically. Before, it was briefly readable by other users on the machine under the default umask, and a symlink at the backup path was followed and its target overwritten. (#79)

### Changed

- New license keys activate through Gumroad only. The Lemon Squeezy fallback, which could accept a key that Gumroad did not recognise, is retired for new activations. Licenses already activated through Lemon Squeezy keep working and keep being re-checked. (#79)
- PRIVACY.md, docs/ONBOARDING.md, AGENTS.md and docs/PRODUCTION_READINESS.md now describe the license re-check accurately. Pro is re-checked with the provider at most once a day, without using an activation, until the first successful check on or after purchase + 21 days. Until then, a refund, chargeback or disabled key reported by the provider switches the machine back to Free. A network failure never revokes, so a machine offline at day 21 keeps re-checking daily until one check succeeds. After that, only `samurai license --refresh` checks again. (#81)

### Fixed

- A Lemon Squeezy license reported as `disabled` or `refunded` now revokes Pro at the next re-check, the same as a Gumroad refund. Before, those answers were read as "provider unreachable" and Pro was kept. Network errors, non-JSON error responses and an unknown key still keep Pro, so going offline never costs you Pro. (#79)

## [2.2.0] - 2026-10-11

### Added

- `LLMGateway.generate_strict()` and `StrictCallError` (`agentica_core/llm/gateway.py`): an opt-in API that makes one isolated Claude CLI call with no tools, no MCP servers, no provider fallback and no prompt telemetry. The request is bounded (prompt at most 24,000 bytes, system prompt at most 8,000 characters, `timeout_s` at most 45, `max_output_chars` at most 4,000, optional `json_schema` at most 8,000 characters as JSON) and the model must be `claude-sonnet-4-6` (the default) or `claude-sonnet-5-5`; anything else raises `StrictCallError`. Nothing in Order Samurai calls it yet. (#58, #72)
- `call_routed_llm()` (also importable as `agentica_core.model_router.call_llm`) accepts two optional arguments: `num_ctx=` (an explicit Ollama context size) and `force_json=` (asks for JSON output). Both are off by default. See Changed for the argument-order change. (#58)
- Three new Autonomic count metrics, lower is better: Stale_Branch_Count ("Stale Branches"), Lane_Pending_Age ("Tickets Stuck at the Lane") and Approved_Unexecuted_HITL ("Approved, Never Executed"). They read only the AgenticaOS development layout, so **on a standard install all three show SIMULATED with no value** and are never flagged. They never run `git` in, or read a ledger from, a folder outside the install, and Approved_Unexecuted_HITL reads SIMULATED even when `state/hitl_queue.json` exists. Their remediation hints name `/git-hygiene` and `/captain`, which Order Samurai does not ship. (#65)
- Mechanism_Liveness gains an `idle_by_design` state. A week reports it (ungraded, live tier, with a `state` field in the metric envelope) instead of 0 / FAIL only when all three hold: no `mechanism_run` events that week, at least one reflex `[starved]` heartbeat that week in `state/reflex_output.jsonl`, and `state/exec_log.jsonl` positively shows no engine run that week. A missing exec log counts as no runs; an exec log that cannot be read, or that has a non-blank line that is not a JSON object, keeps the FAIL. The dashboard card shows no value for this state and does not show its explanation yet. (#65)
- History carry-forward, on by default: when the dashboard snapshot rebuilds history (`agentica_core/backfill_history.py`), metrics that cannot be recomputed from telemetry (scout counts, run-log and security signals) keep their earlier values on the rebuilt weeks, never a later week's value, and those rows list them under `carried_forward`. The newest `kind: "live"` row survives the rebuild. A week recorded as idle stays idle after its heartbeat evidence rotates away, unless the rebuild finds activity that week (a value above 0, or an engine run in `state/exec_log.jsonl`), the exec log exists but cannot be read, or it has a non-blank line that is not a JSON object; then the week is rebuilt from the telemetry that remains. Kill switch: `BACKFILL_CARRY_FORWARD=false` (also `0` or `no`, in any case) turns off all three and restores the old truncate-and-rebuild. (#66)
- Usage windows, their models and each tier carry an explicit `usage_known` flag, true only when every token-bearing event is marked known; mixed or unmarked usage reads false instead of leaving the flag out. (#59)
- `execution/verify_falsifiability.py --json` reports `covered`: the number of registered checks with a fixture pair on disk, whether or not they pass. (#77)
- The zip now carries the characterization tests and snapshot fixtures for the refactored modules (see Changed), the aggregate golden-payload tests, and `metric_disposition.json` at the root, an inert data file read only by the golden-test fixture builder. (#70-#75)

### Changed

- **Argument order of `call_routed_llm()` / `agentica_core.model_router.call_llm`.** The new `num_ctx` and `force_json` parameters sit before `project`, so a caller that passes `project` positionally (as the eighth argument) now binds it to `num_ctx`. Pass `project=` by keyword; Order Samurai's own callers already do. (#58)
- Cache_Hit_Rate is stricter: a usage record counts only when it is marked `usage_known` and reports an explicit `cache_creation_tokens` figure (a missing figure is no longer read as 0). Other records are left out, and the detail line says how many observations were valid. If none qualify, the metric shows no data instead of a number. The native-transcript collector marks its records, so the dashboard number is expected to be unchanged there; the reducer that reads raw telemetry rows without those markers now reports no data. (#59, #60)
- When a call asks for JSON, requests routed through OpenRouter now send `response_format: {"type": "json_object"}`, like the other providers. (#58)
- Skill_Routing_Adherence ignores the hiring-review nudge and credits a detection only when the skill was invoked in the same session at or after it; an invocation without a timestamp no longer credits anything. Governance_Work_Volume also excludes hiring-review detections, so a window with only those reads no data (SIMULATED). In both, a timestamp without a timezone is read as local time (previously UTC), and offsets such as `-0400` also parse on Python before 3.11. Values can change where `~/.claude/data/skill_routing.jsonl` exists. (#65)
- `aggregate` loads licensing lazily and reports `{"tier": "free"}` if the licensing module is missing; when it imports, `dashboard_summary()` is used unchanged, so Pro installs are unaffected. (#65)
- `execution/verify_live_sources.py` resolves sources declared with leading `../` against the parents of the install folder and checks them, instead of exempting them. (#65)
- History rows: every rebuilt weekly row is labelled `kind: "weekly"` (the old rebuild wrote no `kind`), and rows may carry optional `idle` and `carried_forward` lists. Delta and trend arrows, sparklines and the 24-hour movers line skip `kind: "weekly"` rows, so they compare the current 30-day value with live rows rather than one-week totals. Rows with no `kind` still count, so weekly rows written by earlier versions take part until a successful rebuild relabels them. After a successful snapshot rebuild that run has at most 2 points per metric, so σ-anomaly and trajectory warnings fire only once live rows accumulate between snapshot runs, or when a rebuild is skipped or fails. Carried-forward values join the pool `calibrate()` reads, so a metric can gain data-derived thresholds after enough rebuilds; calibration still never loosens a threshold past its manual value. (#66)
- Daily digest (`bin/hitl_alerts.py`): the "Review:" link is now `http://localhost:4322/` (was `http://127.0.0.1:4322/`, the same local dashboard), and the skip message ends "or failing in automation health — no email today". The new automation-health digest section and its `HITL_AUTOMATION_HEALTH_ALARM` opt-out have no effect on a standard install, which does not include the automation-health module. `_digest_body()` gains the optional keyword arguments `automation_health` and `automation_health_monitor_status_fn`, and `decide_email()` gains `automation_problems`; they change the digest text and the send decision only when a caller passes them. (#68)
- `execution/verify_falsifiability.py --json`: the `note` text uses `--` where it had an em dash. (#77)
- `agentica_core/aggregate.py` is split into a facade plus six modules: `agentica_core/aggregate_records.py`, `agentica_core/aggregate_metrics.py`, `agentica_core/aggregate_signals.py`, `agentica_core/aggregate_counters.py`, `agentica_core/aggregate_estimates.py` and `agentica_core/aggregate_scoring.py`. Every name callers imported from `agentica_core.aggregate` is still there, `aggregate()` output is byte-identical before and after on the recorded golden payloads, and `python -m agentica_core.aggregate` now runs `agentica_core.aggregate.main()` with the same default-window output. (#75)
- Internal refactors with no behavior change, each pinned by characterization tests that pass before and after: `usage_records` (#70), `remediation.efficacy()` (#71), `LLMGateway.generate_strict()` and the digest body in `hitl_alerts.py` (#72), `scouts.security_signals()` and `scouts.knowledge_signals()` (#73), `reflex_eureka.analyze()` and `insights.populate_history()` (#74).
- The out-of-package import guard (`tests/test_import_boundary.py`) is hardened: it scans the shipped file set (`git ls-files` in a checkout, a disk walk in an unzipped install), fails closed on loader paths it cannot resolve, inspects Python subprocess calls, and fails on a shipped `.py` file that does not parse. Its registry, `config/out_of_package_imports.json`, grows from 86 to 152 sites, and a new gap test file ships with it. Tests and CI only; no runtime change. (#77)

### Fixed

- A transcript deleted while the dashboard scans context cliffs or agent spawns is skipped instead of failing the whole refresh. (#65)
- A missing, unreadable or non-numeric vibe score stays no data: the Craft detail reads "Vibe no data" and contributes zero estimated hours, instead of a made-up score or an error. (#65)
- A telemetry tag counts toward at most one project (exact or alias match before substring), so session counts and other per-project sums are no longer duplicated. Project scores can change on existing installs. (#65)
- A history rebuild with no readable telemetry, or no weeks, leaves the existing history untouched; it used to rewrite it empty and lose sparklines and baselines. (#66)
- The history store is read, merged and rewritten under the same lock live appends use, and fsynced before unlocking, so a live row appended during a rebuild is neither torn nor lost. Where file locks are unavailable the rebuild falls back to unlocked. (#66, #69)
- If writing or fsyncing the rebuilt history fails (for example a full disk) or is interrupted (Ctrl-C, `SystemExit`, `MemoryError`), the previous bytes are written back before the error propagates, instead of leaving a truncated store. This is best effort; a process killed outright gets no restore. (#66)
- `refresh_dashboard.py` prints one stderr line, `WARN: codex_emit failed: <ExceptionType>`, when the optional Codex emitter (`~/.codex/scripts/codex_emit.py`) is installed and exits non-zero, times out or cannot start; the refresh continues. It used to ignore the failure silently. Only the exception type is printed, never the emitter's output. (#76)
- `execution/verify_falsifiability.py`: a check that raises now reports `detail` as the exception type only (plus the module name for an `ImportError`), capped at 200 characters. It used to include the exception message, which could carry a secret into `--json` output. Statuses and exit codes are unchanged. (#77)

## [2.1.4] - 2026-10-11

### Added

- Support and feedback now go to one address, `support@agentica-llc.biz`, replacing the old ordersamurai.ai support address in the terms, EULA, onboarding guide, Gumroad receipt text and dashboard. It is also printed as the last line of `samurai doctor` (pass or fail; exit codes unchanged) and of both installer success paths, and has a "Feedback & support" section in the README and onboarding guide.
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

- Release builds never pack the release record (`dist/build-record.json`) into the zip, and the version test reads the expected version from `pyproject.toml`.
- The source tree now carries an import-boundary test and registry (`tests/test_import_boundary.py`, `config/out_of_package_imports.json`) that fail CI when shipped code loads something from outside the package without a declared, visible fallback. Tests only; no runtime change.
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

- The daily digest email no longer falls back to Mail.app silently. When `RESEND_API_KEY` is not set and the Keychain lookup fails, it prints one line on stderr first, `WARN: RESEND_API_KEY is not set and the Keychain lookup failed (<ExceptionType>); the digest will use Mail.app`. It never prints the key.
- `samurai doctor` hook-health rows now say why criticality is unknown when the optional hook registry cannot be loaded: each `claude-hook-health` row ends with `; hook_registry unavailable (<ExceptionType>), criticality cannot be read`. Row statuses (OK/WARN/FAIL) do not change.
- `execution/verify_falsifiability.py` no longer dies with a traceback when one check crashes. It reports that check as `error`, finishes the others, and exits 2 when there are only errors (still 1 when a check fails).
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
