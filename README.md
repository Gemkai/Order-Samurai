# Order Samurai ⚔️

> **The Local-First Governance & Security Layer for Autonomous Coding Agent Fleets**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-830%2B%20passed-brightgreen.svg)](tests/)
[![Security Posture](https://img.shields.io/badge/posture-fail--closed-red.svg)](SECURITY.md)

**Order Samurai** turns unmonitored agent execution into a secure, auditable, and business-meaningful engine. It wraps agent runtimes (such as Claude Code) with real-time security hooks, secret scrubbing, prompt injection defense, and provenance-transparent business metrics — entirely local, with **zero external telemetry**.

---

## 🚀 Quickstart (1-Command Install)

Install in **under 60 seconds** on macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/order-samurai/order-samurai/main/install.sh | bash
```

Or clone and run locally:

```bash
git clone https://github.com/order-samurai/order-samurai.git
cd order-samurai
./bin/samurai install
```

Verify your installation with the diagnostic doctor:

```bash
samurai doctor
```

---

## 🛡️ Why Order Samurai?

When autonomous coding agents run in your developer environment, they read files, invoke bash commands, make API calls, and modify source code. Order Samurai provides:

1. **14-Chain ATT&CK Security Interception**: Intercepts indirect prompt injections (Chain 13), credential exfiltration, internal IP exposure, and database URI leaks (Chain 14).
2. **Zero Cloud Telemetry (Local-First)**: Your prompts, source code, and telemetry stay on your machine (`~/.samurai/`). Vendor sees zero code.
3. **4 Business Pillar Metrics**:
   - 🗡️ **SWORD**: *Kill Chains Disrupted* (Resilience count of blocked attack vectors)
   - 🏹 **BOW**: *Agent Time Saved* (Wall-clock operations efficiency)
   - 🎨 **BRUSH**: *Actual Cost Savings* (Token spend & model routing efficiency)
   - 🎭 **ARTS**: *Human Time Saved* (Documentation parity & code alignment)
4. **Honesty Invariant**: Every metric explicitly displays whether it is **MEASURED** (from real system execution) or **SIMULATED** (calibration benchmark placeholder). We never sell fake precision.
5. **Fail-Closed Security Posture**: Security gates fail closed loud (`BUSHIDO_FAIL_OPEN=false`), protecting your repository against silent bypasses.

---

## 💻 CLI Tools & Utilities

Order Samurai ships with a zero-residue management tool:

```bash
# Check environment health, hook registration, and path integrity
samurai doctor

# Install & register settings into ~/.claude/hooks/settings.json (with automatic backup)
samurai install

# Safely uninstall hooks, restore prior settings, and optional zero-residue cleanup
samurai uninstall
```

---

## 📊 Web Dashboard & Landing Page

Order Samurai features a real-time web dashboard built with React + Vite:

```bash
# Launch local dashboard server
cd dashboard-ui
npm install
npm run dev
```

Visit `http://localhost:5173` to view real-time metrics, radar charts, active reflexes, and the interactive product landing page.

---

## 🏗️ Architecture Overview

```
 ┌──────────────────────────────────────────────────────────┐
 │                  Developer Workstation                   │
 │                                                          │
 │   ┌──────────────┐         ┌─────────────────────────┐   │
 │   │ Claude Code  │ ──Pre──>│  prompt_injection_guard │   │
 │   │  (or Agent)  │ <─Post─ │ secret_scrubber_realtime│   │
 │   └──────┬───────┘         └────────────┬────────────┘   │
 │          │                              │                │
 │          ▼                              ▼                │
 │   ┌──────────────────────────────────────────────────┐   │
 │   │          ~/.samurai / Atomic State Logs         │   │
 │   │ (kill_chain_events.jsonl | DOJO_STATE.json)      │   │
 │   └────────────────────────┬─────────────────────────┘   │
 │                            │                             │
 │                            ▼                             │
 │   ┌──────────────────────────────────────────────────┐   │
 │   │             agentica_core.aggregate              │   │
 │   │    (SWORD | BOW | BRUSH | ARTS Reducer Engine)   │   │
 │   └────────────────────────┬─────────────────────────┘   │
 │                            │                             │
 │                            ▼                             │
 │   ┌──────────────────────────────────────────────────┐   │
 │   │           Dashboard UI / Honesty Table           │   │
 │   └──────────────────────────────────────────────────┘   │
 └──────────────────────────────────────────────────────────┘
```

---

## 📄 Documentation & Resources

- 📖 [Honesty Table & Metric Provenance](docs/HONESTY_TABLE.md)
- 🛡️ [Security Policy](SECURITY.md)
- 📜 [Terms of Service](TERMS.md)
- 🔒 [Privacy Policy (Zero Telemetry)](PRIVACY.md)
- ⚖️ [End User License Agreement (EULA)](EULA.md)
- 🤝 [Contributing Guidelines](CONTRIBUTING.md)
- 📝 [Changelog](CHANGELOG.md)

---

## ⚖️ License & Commercial Pro Tier

* **Open Source Core**: Licensed under the [Apache License 2.0](LICENSE). Free forever for four-pillar scoring and fail-closed CLI security hooks.
* **Order Samurai Pro ($199 Lifetime License)**: Includes Nightly Dojo automated regression runs, autonomous reflex remediation, and offline perpetual key activation. Backed by a **14-day 100% money-back guarantee**. See [TERMS.md](TERMS.md) and [EULA.md](EULA.md).

