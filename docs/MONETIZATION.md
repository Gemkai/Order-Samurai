# Order Samurai — Monetization & Growth Strategy

Order Samurai is a local-first, fail-closed AI agent governance and security system designed for autonomous coding-agent fleets. 

This document defines the comprehensive monetization strategy, channel distribution, compliance expansion roadmap, and local-first MCP architecture boundaries.

---

## 1. Executive Summary & Revenue Architecture

Order Samurai uses an **Open-Core / Dual-Licensing & Tiered SaaS Model** designed to maximize top-of-funnel developer adoption while capturing high-margin commercial and enterprise revenue.

```
 ┌────────────────────────────────────────────────────────────────────────────────────────┐
 │                              ORDER SAMURAI REVENUE MATRIX                              │
 ├──────────────────────────┬──────────────────────────┬──────────────────────────────────┤
 │    1. OSS Open-Core      │   2. Pro Lifetime Tier   │     3. Enterprise Tier         │
 │       $0 / Forever       │      $199 / One-Time     │       $499 / Month / Team        │
 │   Apache-2.0 License    │    Proprietary License   │     Annual/Monthly SaaS          │
 └──────────────────────────┴──────────────────────────┴──────────────────────────────────┘
```

---

## 2. Tier Breakdown & Feature Matrix

| Feature / Capability | Open Core ($0) | Pro Lifetime ($199) | Enterprise ($499/mo) | OEM / B2B SDK |
| :--- | :---: | :---: | :---: | :---: |
| **14-Chain ATT&CK Monitors** | ✅ Fail-Closed | ✅ Fail-Closed | ✅ Fail-Closed | ✅ Fail-Closed |
| **Real-time Secret Scrubber** | ✅ Local | ✅ Local | ✅ Local | ✅ Local |
| **Four-Pillar Metrics Dashboard** | ✅ Local | ✅ Local | ✅ Local + Team | ✅ Embedded |
| **Local MCP Governance Server** | ✅ Basic Tools | ✅ Full Toolset | ✅ Full Toolset | ✅ Custom Tools |
| **Nightly Dojo & Autonomous Reflexes** | — | ✅ Full Autonomy | ✅ Full Autonomy | ✅ Custom Rules |
| **Maker-Checker Patch Staging** | — | ✅ Local Staging | ✅ Team Approval | ✅ Customizable |
| **Compliance Packs (NIST / EU AI Act)** | — | — | ✅ Automated | Optional Add-on |
| **Multi-Project Fleet Aggregation** | — | — | ✅ Centralized | Custom |
| **Auditor Signed Evidence (.zip)** | — | — | ✅ Exportable | Optional Add-on |

---

## 3. Local-First Architecture vs. Revenue Channels

### 🔌 Local MCP Server (Primary Agent Integration & Upsell Vector)
* **Architecture**: Order Samurai exposes a **Model Context Protocol (MCP) server** running entirely on the developer's workstation via local IPC / standard I/O.
* **Privacy Boundary**: **100% Local**. No prompts, code, or telemetry ever leave the machine.
* **How it Drives Revenue**:
  * Allows AI agents (Claude Code, Cursor, Codex, Gemini CLI) to query policy and spend status *before* executing shell commands or tool calls.
  * **Free MCP Tools**: Basic policy queries and secret scrubbing checks.
  * **Pro / Enterprise MCP Tools ($199 / $499)**: Autonomous remediation triggers, budget ledger queries, and compliance audit exports via MCP.

### 🛑 Why We Avoid Centralized Cloud APIs
* Streaming logs or code to a central cloud server destroys Order Samurai's core value proposition: **Zero Cloud Telemetry & Complete Data Privacy**.
* **Self-Hosted Enterprise Hub Option**: For enterprise teams requiring centralized fleet monitoring, Order Samurai offers a **Customer-Hosted VPC Deployment** (deployable on their private AWS/GCP/Azure tenant), preserving strict data boundaries.

---

## 4. Compliance Packs Roadmap ($499/mo Enterprise Value)

Compliance Packs translate real-time local security logs into structured, exportable regulatory reports and auditor evidence bundles.

```
 Telemetry Ledger                     Compliance Engine                   Auditor Deliverable
 ┌────────────────────────┐          ┌────────────────────────┐        ┌─────────────────────────┐
 │ • kill_chain_events    │          │  Compliance Mapping    │        │ • NIST AI RMF Audit PDF │
 │ • secret_scrubs        │ ───────► │  Engine                │ ─────► │ • EU AI Act Art.14/15   │
 │ • budget_ledger        │          │  (agentica_core)       │        │ • OWASP Cross-Walk      │
 │ • dojo_meditations     │          └────────────────────────┘        │ • Signed Evidence .zip  │
 └────────────────────────┘                                            └─────────────────────────┘
```

### Supported Frameworks:
1. **NIST AI RMF 1.0 (Govern, Map, Measure, Manage)**: Maps SWORD injection blocks and Dojo runs to NIST risk control categories.
2. **EU AI Act (Article 14 & Article 15)**: Provides evidence of human oversight (maker-checker patch staging) and cybersecurity robustness.
3. **OWASP Agentic Top 10**: Cross-walk mapping of intercepted injection attempts and secret scrubs to LLM01, LLM02, and LLM10 risks.
4. **SOC 2 Type II Evidence Bundles**: One-click signed `.zip` export containing SHA-256 hashed event ledgers and configuration policy manifests.

---

## 5. Marketplaces & Distribution Channels

### 🛒 Developer & Merchant of Record (MoR) Marketplaces
* **Gumroad** *(Active)*: Primary B2C storefront for instant developer lifetime purchases (`$199`).
* **Lemon Squeezy** *(Integrated)*: Automated license validation & machine activation engine (`samurai activate <key>`) with global VAT/sales tax compliance.
* **Paddle**: Backup Merchant of Record for global B2B payments.

### 💻 IDE & Extensions Marketplaces
* **VS Code Extension Marketplace**: Thin client extension wrapping the local dashboard for in-editor agent governance.
* **JetBrains Marketplace**: Targeted distribution for enterprise PyCharm, WebStorm, and IntelliJ environments.
* **GitHub Marketplace**: CI/CD integration as a GitHub Action for repository security scanning.

### ☁️ Enterprise Cloud Marketplaces
* **AWS / Google Cloud / Azure Marketplaces**: Enables enterprise engineering teams to procure Order Samurai against pre-committed cloud spend commitments (EDP).
