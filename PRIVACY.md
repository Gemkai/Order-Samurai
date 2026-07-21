# Order Samurai — Privacy Policy

*Effective Date: July 20, 2026*

At **Order Samurai**, privacy is not a feature — it is a core architectural invariant. Order Samurai is engineered to govern coding-agent fleets **entirely on your local machine** with **zero cloud telemetry**.

---

## 1. Zero Cloud Telemetry Architecture

- **100% On-Device Storage**: All telemetry, metric snapshots, agent session logs, secret scrubbing events, and Dojo state files are stored locally in your filesystem (`~/.samurai/` or project-relative `.tmp/`).
- **No Remote Event Tracking**: Order Samurai does not ping remote tracking endpoints, Google Analytics, Mixpanel, or custom telemetry servers.
- **No Code or Prompt Harvesting**: Your source code, subagent prompts, internal documentation, and LLM conversations are never uploaded to any remote server by Order Samurai.

---

## 2. Information Handled During Commercial Checkout (Pro Tier)

When purchasing Order Samurai Pro ($199 Lifetime License) via our Merchant of Record (**Lemon Squeezy**):

- **Payment Data**: Payment processing is handled securely by Lemon Squeezy and Stripe. Order Samurai maintainers never store or transmit raw credit card or banking details.
- **Customer Email & License Key**: Lemon Squeezy collects your email address to issue your digital receipt, tax invoice, and Order Samurai Pro license key.
- **Offline License Key Verification**: License validation checks run locally using cryptographic public key signatures. No heartbeat pings or telemetry are transmitted during offline CLI execution.

---

## 3. Local Data Rights & Control

Because all data remains on your local filesystem, you have total control over your governance records:
- **Deletion**: Running `rm -rf ~/.samurai/` completely purges all local state, logs, and metric history from your machine.
- **Inspection**: All state files (`wid_payload.json`, `autonomic_events.jsonl`, `kill_chain_events.jsonl`) are open, human-readable JSON/JSONL format.

---

## 4. Updates & Security Policy

Order Samurai may periodically check GitHub Releases via standard `git` or CLI package managers for available software updates. No user identifying information is attached to public release checks.

---

## 5. Contact Us

If you have any privacy questions or concerns:
- **Email**: `privacy@ordersamurai.dev`
- **Security**: `security@ordersamurai.dev`
