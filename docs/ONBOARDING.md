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
checkout's `bin/` to your PATH for the `samurai` shorthand. Launch Claude Code from
that activated shell: the hooks invoke `python3` and need those dependencies on its
interpreter path. The current hook command requires a permanent checkout path
without spaces.

`samurai install` registers the security hooks into `~/.claude/settings.json` (it backs up
any existing settings to `~/.samurai/backups/` first) and writes an install marker to
`~/.samurai/install.json`.

### 2. Verify

```bash
samurai doctor
```

Require `samurai doctor` to exit successfully and show registered hooks plus
`License Tier: FREE`. Missing hook registration is a failed installation, not an
expected healthy state. Doctor checks configuration; verify supported hooks in your
actual agent workflow before relying on protection.

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

```bash
samurai activate <your-license-key>
```

This validates the key online **once** via Gumroad, registers this machine, and
writes your entitlement to `~/.samurai/license.json`. After that it is an **offline
perpetual** license — the Pro features work with no network connection, forever, on this
machine.

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
| `samurai activate` says "license key invalid" | Check for typos/whitespace; copy the key from your Gumroad receipt. Refunded keys are rejected. |
| Dojo says *"is an Order Samurai Pro feature"* | You are on Free. Run `samurai activate <key>` (or buy one) to unlock. |
| Legacy `REFLEX_AUTO_APPLY=true` setting | Remove it. Auto-apply is retired on both tiers; review staged patches before explicitly approving a repair. |
| Want to remove everything | `samurai uninstall` (add `--keep-data` to preserve `~/.samurai`). |

---

## Reference

- Install / CLI: [README.md](../README.md)
- Metric provenance: [docs/HONESTY_TABLE.md](HONESTY_TABLE.md)
- Terms & refunds: [TERMS.md](../TERMS.md) · [EULA.md](../EULA.md)
- Privacy (zero telemetry): [PRIVACY.md](../PRIVACY.md)
