# Onboarding — Order Samurai

Order Samurai has two tiers. **Free** is fully functional and installs in under a minute.
**Pro** ($199 lifetime) unlocks the autonomous engines. This guide covers onboarding for
both, plus verification and troubleshooting.

> Everything runs **locally**. No account, no cloud sign-up, no telemetry leaves your
> machine. State lives in `~/.samurai/`.

---

## Tier comparison

| Capability | Free (Apache-2.0) | Pro ($199 lifetime) |
|---|---|---|
| 14-chain ATT&CK security interception (prompt-injection, secret exfil) | ✅ | ✅ |
| Real-time secret scrubbing | ✅ | ✅ |
| Fail-closed security posture | ✅ | ✅ |
| Four-pillar metrics + web dashboard | ✅ | ✅ |
| Honesty invariant (MEASURED vs SIMULATED labels) | ✅ | ✅ |
| **Nightly Dojo** — autonomous overnight regression runs | — | ✅ |
| **Autonomous reflex remediation** — auto-apply validated patches | — | ✅ |
| **Maker-checker patch staging** | — | ✅ |
| **Extended telemetry time windows** | — | ✅ |
| 14-day money-back guarantee | — | ✅ |

Free is not a trial — it is a complete, supported product. Pro adds autonomy on top.

---

## Part 1 — Free onboarding (everyone starts here)

### 1. Install

One-command install (macOS / Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/order-samurai/order-samurai/main/install.sh | bash
```

Or clone and install locally:

```bash
git clone https://github.com/order-samurai/order-samurai.git
cd order-samurai
./bin/samurai install
```

`samurai install` registers the security hooks into `~/.claude/settings.json` (it backs up
any existing settings to `~/.samurai/backups/` first) and writes an install marker to
`~/.samurai/install.json`.

### 2. Verify

```bash
samurai doctor
```

A healthy Free install reports **4/5 checks passed** plus a `License Tier: FREE` line. The
one expected non-pass on a brand-new machine is *Claude Code Hook Registration* until the
first `samurai install` completes — after install it turns green.

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

Pro is a superset of Free — you keep everything above and add the autonomous engines. There
is nothing to reinstall; you activate a license key on an existing Free install.

### 1. Buy a license

Purchase the **$199 Pro Lifetime License** at
<https://ordersamurai.lemonsqueezy.com>. Checkout is handled by Lemon Squeezy and backed by
a **14-day 100% money-back guarantee** (see [TERMS.md](../TERMS.md) and [EULA.md](../EULA.md)).
You receive a license key by email immediately after purchase.

### 2. Activate

```bash
samurai activate <your-license-key>
```

This validates the key online **once** with Lemon Squeezy, registers this machine, and
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

- **Nightly Dojo** (overnight autonomous regression engine):
  ```bash
  ./bin/dojo_overnight.sh
  ```
- **24/7 ronin daemon**:
  ```bash
  ./bin/ronin-daemon.sh
  ```
- **Autonomous reflex remediation** (auto-apply validated patches instead of staging them
  for review): set `REFLEX_AUTO_APPLY=true` before starting the API server. This env var
  only takes effect **with a valid Pro license** — on Free it stays in safe review-only mode
  no matter what.

### Moving to a new machine / refunds

- **New machine**: run `samurai deactivate` on the old machine, then `samurai activate` on
  the new one.
- **Refund** (within 14 days): email `support@ordersamurai.dev`. On refund the key is
  revoked; `samurai license` will report Free again after your next activation check.

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
| `samurai activate` says "license key invalid" | Check for typos/whitespace; confirm the key from your Lemon Squeezy email. Refunded keys are rejected. |
| Dojo says *"is an Order Samurai Pro feature"* | You are on Free. Run `samurai activate <key>` (or buy one) to unlock. |
| `REFLEX_AUTO_APPLY=true` but patches still stage for review | Auto-apply requires **both** the env var and a Pro license — run `samurai license` to confirm PRO. |
| Want to remove everything | `samurai uninstall` (add `--keep-data` to preserve `~/.samurai`). |

---

## Reference

- Install / CLI: [README.md](../README.md)
- Metric provenance: [docs/HONESTY_TABLE.md](HONESTY_TABLE.md)
- Terms & refunds: [TERMS.md](../TERMS.md) · [EULA.md](../EULA.md)
- Privacy (zero telemetry): [PRIVACY.md](../PRIVACY.md)
