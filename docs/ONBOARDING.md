# Onboarding — Order Samurai

Order Samurai has two tiers. **Free** includes the core local checks and dashboard.
**Pro** ($199 lifetime) adds scheduled checks and extended review tools. This guide covers onboarding for
both, plus verification and troubleshooting.

> Analysis runs locally by default, with no product telemetry collected by Order
> Samurai. Dependency installation and Pro activation need network access. Optional
> third-party AI review transmits review content only after explicit opt-in.
> State lives in `~/.samurai/`.

---

## Tier comparison

| Capability | Free (Apache-2.0) | Pro ($199 lifetime) |
|---|---|---|
| 14-chain ATT&CK security interception (prompt-injection, secret exfil) | ✅ | ✅ |
| Real-time secret scrubbing | ✅ | ✅ |
| Fail-closed security posture | ✅ | ✅ |
| Four-pillar metrics + web dashboard | ✅ | ✅ |
| Honesty invariant (MEASURED vs SIMULATED labels) | ✅ | ✅ |
| **Nightly Dojo** — scheduled regression checks and approved backlog work | — | ✅ |
| **Maker-checker patch staging** | — | ✅ |
| **Extended telemetry time windows** | — | ✅ |
| 14-day money-back guarantee | — | ✅ |

Free is not a trial — it is a complete, supported product. Pro adds scheduled checks, review tools, and extended telemetry.

---

## Part 1 — Free onboarding (everyone starts here)

### 1. Install

One-command install (macOS / Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/Gemkai/order-samurai/main/install.sh | bash
```

The website installer downloads and verifies the archive; it does not install runtime
dependencies or register hooks. Extract the ZIP into a permanent path without spaces,
then follow the setup below. Alternatively, clone the public source:

```bash
git clone https://github.com/Gemkai/order-samurai.git
cd order-samurai
python3 -m venv .venv
source .venv/bin/activate
PYTHON="$PWD/.venv/bin/python" bash bin/install.sh
python bin/samurai install
python bin/samurai doctor
```

Python **3.11+** is required. `bin/install.sh` installs dependencies and generates the
first report; `samurai install` separately registers hooks. Stop if either command
fails. Keep the virtual environment activated in future sessions, and add this
checkout's `bin/` to your PATH for the `samurai` shorthand. Launch the dashboard and
Pro scripts from that activated shell: they run `python3` and need those dependencies
on its interpreter path. The hooks do not: they use only the Python standard library.
The current hook command requires a permanent checkout path without spaces.

Homebrew and Linux distribution Pythons refuse `pip install` (PEP 668). Run on one of
them without a virtual environment, `bin/install.sh` puts the dependencies in a private
environment at `~/.samurai/venv` instead.

`samurai install` registers the security hooks for every agent harness it finds and prints
one result line per harness:

- **Claude Code** (found when `~/.claude` exists or `claude` is on your PATH): the
  prompt-injection guard and the secret scrubber go into `~/.claude/settings.json`.
- **Codex** (found when `codex` is on your PATH or the ChatGPT app's bundled Codex is
  installed): the prompt-injection guard is appended to `$CODEX_HOME/hooks.json`
  (default `~/.codex/hooks.json`). Codex skips a new hook until you approve it: open
  Codex, run `/hooks` and approve the Order Samurai hook. Your existing Codex hooks keep
  their content, order and approvals.

- **Cursor** (found when `cursor` or `cursor-agent` is on your PATH or `Cursor.app` is in
  `/Applications`): the prompt-injection guard is added to `~/.cursor/hooks.json` for
  `beforeShellExecution`, `beforeMCPExecution` and `preToolUse` (file writes), marked
  `failClosed` so a crashed or timed-out guard blocks instead of letting the action through.
  A `~/.cursor` folder alone does not count as an install and is skipped. Restart Cursor
  (or reload the window) so it re-reads the file. Your other Cursor hooks keep their
  content and order.
- **Gemini CLI** (found when `gemini` is on your PATH): the prompt-injection guard is added
  to the `hooks` section of `~/.gemini/settings.json` as a `BeforeTool` hook. That file
  also holds your auth, theme, MCP servers and every other Gemini setting; Order Samurai
  changes only its own hook entry, keeps the file's permissions and backs it up first. A
  `~/.gemini` folder alone does not count as an install and is skipped (other Google tools
  such as Antigravity also write there). Restart Gemini CLI so it re-reads the file. Your
  other Gemini hooks keep their content and order.

To choose harnesses yourself, run `samurai install --harness claude`, `--harness codex`,
`--harness cursor`, `--harness gemini` or a comma list such as `--harness claude,gemini` (or set `SAMURAI_HARNESS`). The choice is remembered on later
installs. Existing configs are backed up to `~/.samurai/backups/` before any change, and
`~/.samurai/install.json` records exactly what was written so uninstall removes only that.

**What the Codex guard covers.** It scans the `Bash` and `apply_patch` calls Codex sends to
`PreToolUse` hooks (for `apply_patch`, only the lines a patch adds and its file names). It
does **not** cover input written with `write_stdin` to an already-running shell, MCP tools,
other tool routes, or anything at all while hooks are disabled in Codex
(`[features] hooks = false` or a managed policy). The secret scrubber is not mirrored to
Codex.

**What the Cursor guard covers.** It scans shell commands (`beforeShellExecution`), MCP tool
calls (`beforeMCPExecution`) and `Write` tool calls (`preToolUse`) that the Cursor desktop app
sends to hooks. It does **not** cover file reads, other tools, Tab completions, or any hook
level above or beside the user file (enterprise, team and project `hooks.json` are never
touched). Cursor documents hooks for its desktop app; whether the `cursor-agent` command-line
tool runs all of these events is not confirmed, and community reports say it wires fewer of
them, so do not assume the CLI is covered until you have tested it. Order Samurai writes no
secret-scrubber hook for Cursor.

**Cursor also runs your Claude Code hooks.** Cursor's third-party-hooks import
(Cursor Settings, Agents, Third-Party Imports, "Include Third-Party Plugins, Skills, and Other
Configs", on by default) loads `~/.claude/settings.json`, mapping `PreToolUse` and
`PostToolUse` to `preToolUse` and `postToolUse`. With Claude Code installed, Cursor therefore
also runs the Claude guard (not `failClosed`: a crash there lets the call through) and the
Claude secret scrubber, so a write can be scanned twice. That is harmless, but it means the
`failClosed` entries above are the ones Cursor enforces strictly. Switching that setting off
leaves only the entries in `~/.cursor/hooks.json`.

**Before relying on Cursor protection.** The registered command is `python3 '<path>'`, resolved
from the environment Cursor itself was launched with: on macOS a GUI app often has a shorter
`PATH` than your shell, and `/usr/bin/python3` is a stub that does nothing without the Command
Line Tools. Because the entries are `failClosed`, a `python3` Cursor cannot find blocks every
shell, MCP and `Write` call. Run a harmless command in Cursor after installing and confirm it
still works, then confirm a command containing a known injection phrase is refused.

**What the Gemini CLI guard covers.** It scans the arguments of the shell tool
(`run_shell_command`), the file-writing tools (`write_file`, `replace`) and every MCP tool
(`mcp_<server>_<tool>`) that Gemini sends to `BeforeTool` hooks, including JSON carried
inside an argument string. It does **not** cover `read_file` (reading Order Samurai's own
pattern files must keep working), `web_fetch`, web search, other built-in tools, hooks in
other settings layers (project `.gemini/settings.json`, system settings, extensions), or
anything while hooks are switched off (`hooksConfig.enabled: false`, or the hook listed in
`hooksConfig.disabled`; doctor reports both). A blocked call exits with code 2 and a
`{"decision": "deny", ...}` document, and a guard failure also exits 2: Gemini lets a call
through on any other non-zero exit code (for example a `python3` it cannot find, exit 127).
Gemini's hook `timeout` is in milliseconds (Order Samurai writes `10000`). Gemini documents
a fingerprint-and-warn step for *project* hooks only; it documents no approval step for
user-level hooks like this one, but that has not been tested on a live install. A
`settings.json` that is not strict JSON (for example one with comments) is refused untouched,
and so is an install path containing `$`, which Gemini expands inside that file. Order
Samurai writes no secret-scrubber hook for Gemini.

**Before relying on Gemini CLI protection.** The registered command is `python3 '<path>'`,
resolved from the environment Gemini CLI runs in. Start Gemini, run a harmless shell command
and confirm it still works, then confirm a command containing a known injection phrase is
refused with Order Samurai's message.

### 2. Verify

```bash
samurai doctor
```

Require `samurai doctor` to exit successfully and show registered hooks plus
`License Tier: FREE`. Missing hook registration is a failed installation, not an
expected healthy state. Doctor checks configuration and runs the packaged hook scripts
directly; verify supported hooks in your actual agent workflow before relying on protection.
For Codex, doctor reports "guard installed and working when run directly; Codex enforcement
not verified by doctor" plus what it can read about your `/hooks` approval: it cannot prove
Codex will run the hook.
For Cursor, doctor reports "guard installed and working when run directly; Cursor enforcement
not verified by doctor": it checks that the entries are present and unmodified and that the
guard answers Cursor-shaped payloads, but cannot prove Cursor runs the hook.
For Gemini CLI, doctor reports "guard installed and working when run directly; Gemini CLI
enforcement not verified by doctor": it checks the entry, that `hooksConfig` in the file does
not switch it off, and that the guard answers Gemini-shaped payloads with valid JSON, but it
cannot prove Gemini CLI runs the hook.

### 3. Launch the dashboard (optional)

```bash
cd dashboard-ui
npm install
npm run dev
```

Open `http://localhost:5173` for live four-pillar metrics, radar charts, and active
reflexes. Every metric is labelled **MEASURED** or **SIMULATED** so you always know what is
real telemetry versus a calibration placeholder.

That's it — Free is protecting your agent sessions. Prompt-injection attempts are blocked
and secrets are scrubbed in real time, logging locally to `~/.samurai/`.

---

## Part 2 — Pro onboarding (upgrade any time)

Pro is a superset of Free — you keep everything above and add scheduled checks and extended review tools. There
is nothing to reinstall; you activate a license key on an existing Free install.

### 1. Buy a license

Purchase the **$199 Pro Lifetime License** at
<https://jemakaib1.gumroad.com/l/sqwomh>. Checkout is handled by Gumroad and backed by
a **14-day 100% money-back guarantee** (see [TERMS.md](../TERMS.md) and [EULA.md](../EULA.md)).
You receive a license key immediately on your receipt and by email.

### 2. Activate

**New install: one command does both.** Run the installer. When it finishes it asks for
your key. Paste it (nothing appears while you paste; that is intentional) and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/Gemkai/order-samurai/main/install.sh | bash
```

You should see `Order Samurai Pro activated (key ****ABCD)`. Press Enter instead to stay
on Free. A mistyped key is asked for again, up to three times.

**Already installed?** Run this and paste the key when asked (installed with the curl
one-liner: `python3 ~/.samurai/core/bin/samurai activate`; from a clone: `bin/samurai activate`):

```bash
samurai activate
```

(`samurai activate <key>` still works, but leaves the key in your shell history. For
scripts, set `SAMURAI_LICENSE_KEY`, or pipe it: `printf %s "$KEY" | samurai activate`.)

Headless installs (CI, Docker): the installer only asks when a terminal is attached. Set
`SAMURAI_LICENSE_KEY` to activate during install, or `SAMURAI_NO_PROMPT=1` (or
`samurai install --no-activate`) to skip the question, e.g. with `docker run -t` and no `-i`.

This validates the key online via Gumroad, registers this machine, and writes your
entitlement to `~/.samurai/license.json`. After that it is an **offline perpetual**
license: the Pro features work with no network connection on this machine.

Confirm it took:

```bash
samurai license
```

You should see `Order Samurai — PRO tier` with your machine name and activation date.
`samurai doctor` will now show `License Tier: PRO`.

### 3. Use the Pro features

- **Nightly Dojo** (scheduled regression checks and explicitly approved backlog work):
  ```bash
  ./bin/dojo_overnight.sh
  ```
- **24/7 ronin daemon**:
  ```bash
  ./bin/ronin-daemon.sh
  ```
- **Repair review**: inspect staged patches and their validation evidence before explicitly
  approving and applying a change. Scheduled checks and a Pro license do not authorize
  unattended LLM-generated repair.

### Moving to a new machine / refunds

- **New machine**: run `samurai deactivate` on the old machine, then `samurai activate` on
  the new one.
- **Refund** (within 14 days): contact the seller through your Gumroad receipt or
  email `support@ordersamurai.ai`. Deactivate the local entitlement after a refund.
  Subsequent activation rejects a refunded key; an already activated offline
  entitlement is not automatically revoked remotely. `samurai license` only reads
  local state and does not refresh refund status.

---

## How the licensing works (transparency)

Order Samurai never phones home to check your license during normal use. The key is
verified **once** at `samurai activate` time; the resulting entitlement lives in
`~/.samurai/license.json` and every Pro feature reads that file locally. The gate is
**fail-closed**: a missing, malformed, inactive, or refunded entitlement always falls back
to Free. The single source of truth is `agentica_core/licensing.py` (Python) and
`api/src/licensing.ts` (the dashboard API) — both read the same file.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `samurai: command not found` | Run from the repo: `./bin/samurai <cmd>`, or add `bin/` to your `PATH`. |
| `samurai doctor` shows *Hook Registration* FAIL | Run `samurai install` (registers the hooks); re-run doctor. |
| Doctor says *registration mismatch* | The hook in your config no longer matches what `samurai install` wrote (edited, moved ambiguously or removed). Doctor did not run it. Restore it or run `samurai install`. |
| Doctor says *no trust record — approve in Codex /hooks* | Open Codex, run `/hooks` and approve the Order Samurai hook. Codex skips it until you do. |
| `samurai uninstall` refuses to remove the Codex hook | Removing it would shift the hooks after it, and Codex would ask you to re-approve them. Run `samurai uninstall --force` to remove it anyway. |
| `samurai activate` says "license key invalid" | Copy the key again from your Gumroad receipt (the Copy button avoids stray spaces) and paste it when asked. Refunded keys are rejected. |
| The key prompt shows nothing when I paste | Expected: input is hidden. Paste once and press Enter. |
| Installer did not ask for a key | It only asks in a terminal. Run `samurai activate` afterwards. |
| Dojo says *"is an Order Samurai Pro feature"* | You are on Free. Run `samurai activate` and paste your key (or buy one) to unlock. |
| Legacy `REFLEX_AUTO_APPLY=true` setting | Remove it. Auto-apply is retired on both tiers; review staged patches before explicitly approving a repair. |
| Want to remove everything | `samurai uninstall` (add `--keep-data` to preserve `~/.samurai`). |

---

## Reference

- Install / CLI: [README.md](../README.md)
- Metric provenance: [docs/HONESTY_TABLE.md](HONESTY_TABLE.md)
- Terms & refunds: [TERMS.md](../TERMS.md) · [EULA.md](../EULA.md)
- Privacy (zero telemetry): [PRIVACY.md](../PRIVACY.md)
