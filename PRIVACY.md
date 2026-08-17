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
- **Offline License Key Verification**: License validation checks run locally using cryptographic public key signatures. No heartbeat pings or telemetry are transmitted during offline CLI execution.

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

Order Samurai may periodically check GitHub Releases via standard `git` or CLI package managers for available software updates. No user identifying information is attached to public release checks.

---

## 5. Contact Us

If you have any privacy questions or concerns:
- **Email**: `privacy@ordersamurai.ai`
- **Security**: `security@ordersamurai.ai`
