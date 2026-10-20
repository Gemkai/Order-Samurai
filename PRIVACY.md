# Order Samurai — Privacy Policy

*Effective Date: July 20, 2026*

At **Order Samurai**, privacy is not a feature — it is a core architectural invariant. Order Samurai performs governance analysis **locally by default**, with **no telemetry sent to Order Samurai**. Optional third-party AI review only occurs when you explicitly enable a cloud LLM provider with your own credentials — see below.

---

## 1. Local-First Analysis, No Telemetry to Order Samurai

- **100% On-Device Storage**: All telemetry, metric snapshots, agent session logs, secret scrubbing events, and Dojo state files are stored locally in your filesystem (`~/.samurai/` or project-relative `.tmp/`).
- **No Remote Event Tracking**: Order Samurai does not ping remote tracking endpoints, Google Analytics, Mixpanel, or custom telemetry servers.
- **No Code or Prompt Harvesting by Order Samurai**: Your source code, subagent prompts, internal documentation, and LLM conversations are never uploaded to any Order Samurai server. If you explicitly configure a third-party cloud LLM provider (e.g. Gemini, Anthropic, OpenRouter) with your own API key for AI-assisted review, prompts are sent directly to that provider under its own privacy terms — Order Samurai does not intermediate, log, or retain that traffic.

---

## 2. Information Handled During Commercial Checkout (Pro Tier)

When purchasing Order Samurai Pro ($199 Lifetime License) via our Merchant of Record:

- **Payment Data**: Payment processing is handled securely by our Merchant of Record. Order Samurai maintainers never store or transmit raw credit card or banking details.
- **Customer Email & License Key**: Our Merchant of Record collects your email address to issue your digital receipt, tax invoice, and Order Samurai Pro license key.
- **License Activation**: `samurai activate` validates current-store keys online with Gumroad and stores the entitlement locally. Existing legacy Lemon Squeezy entitlements are re-checked with Lemon Squeezy; new keys are never sent there. Once the entitlement has settled, normal use reads the record without a heartbeat; it does not verify a cryptographic public-key signature. Refund status is checked at activation and then re-checked with the provider at most once a day, without using an activation, until the first successful check after the 21-day refund window (the 14-day guarantee plus 7 days for processing); a refund, chargeback or disabled key reported by the provider reverts the machine to Free. A network failure never revokes; if no check succeeds after the window, the daily re-checks continue. After that first successful check the entitlement settles and is not checked again unless you run `samurai license --refresh`.

---

## 3. Local Data Rights & Control

Because all data remains on your local filesystem, you have total control over your governance records:
- **Deletion**: `~/.samurai/` also holds `license.json`, your Pro entitlement record. Back it up first if you have a paid license —
  `cp ~/.samurai/license.json ~/license.json.bak` — then run `rm -rf ~/.samurai/` to purge all local state, logs, and metric
  history. **This is irreversible**: without a backup, a purged `license.json` requires re-running `samurai activate <key>`
  to restore Pro access.
- **Inspection**: All state files (`wid_payload.json`, `autonomic_events.jsonl`, `kill_chain_events.jsonl`) are open, human-readable JSON/JSONL format.

---

## 4. Updates & Security Policy

`samurai doctor` and the dashboard refresh check for a newer release at most once a day, on Free and Pro alike. The check is a plain HTTPS GET of the public `dist/build-record.json` file from the same GitHub address the installer downloads from (`raw.githubusercontent.com`). It sends no license key, email, telemetry or other data about you or your machine; like any web request, GitHub sees your IP address. The result is cached in `~/.samurai/update_check.json`. If you are offline the check is skipped silently. Set `SAMURAI_NO_UPDATE_CHECK=1` to turn it off.

---

## 5. Contact Us

If you have any privacy questions or concerns:
- **Email**: `privacy@ordersamurai.ai`
- **Security**: `security@ordersamurai.ai`
