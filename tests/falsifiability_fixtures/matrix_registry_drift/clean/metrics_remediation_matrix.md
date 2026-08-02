# Agentica OS Governance Dashboard: Metrics & Remediation Audit Matrix

This document provides a rigorous, itemized audit of all telemetry and security metrics monitored by the **Agentica OS Governance Dashboard**. Each metric is evaluated under its respective governance pillar and graded based on:
1. **Metric Utility Score (1–10):** The fidelity, relevance, and actionability of the metric itself. High scores indicate direct, critical indicators; low scores denote high noise, gaming susceptibility, or redundant logic.
2. **Remediation Soundness/Safety Score (1–10):** The safety, deterministic reliability, and effectiveness of the associated autonomic skill command. High scores represent idempotent, highly targeted repairs; low scores denote generic prompts, risky code modifications, or indirect mitigations.

---

## 📋 Evaluation Criteria & Scale

### Metric Utility Score (1–10)
*   **9–10 (Critical):** Core security, boundary, or operational stability checks. Immediate action is required if breached. Zero or negligible false-positive rates.
*   **7–8 (High Utility):** Reliable operational, cost, or quality indicators. Essential for health indexing but may exhibit slight noise or minor correlation overlap.
*   **5–6 (Moderate Utility):** Helpful context or protective activity counters, but lacks direct operational urgency or has secondary actionability.
*   **1–4 (Low/Redundant):** Redundant metrics that double-count issues, have extremely high noise profiles, or are prone to gaming.

### Remediation Safety & Effectiveness Score (1–10)
*   **9–10 (Exceptional):** Idempotent, deterministic script-based or tool-based repair. Minimal side effects, verified pathing, and highly safe.
*   **7–8 (High Safety):** Safe automated action gated by dry-runs, or model-routing options that involve human-in-the-loop decisions. High target efficacy.
*   **5–6 (Moderate Safety/Actionable):** Triggers generic `/investigate` routines or diagnostics. Relies on non-deterministic LLM refactoring, which can introduce syntax errors or loop-breaker trips.
*   **1–4 (Unsound/Risky):** Modifies file structures without validation, lacks rate/cooldown gates, or fails to directly address the root telemetry breach.

---

## 🎯 Pillar 1: Way of the Bow (Operations & Autonomy)
Operational stability, latency, throughput, process health, and basic governance verifier pass rates.

The roster below is GENERATED (`python3 docs/regen_metrics_matrix.py`) from the live dashboard
payload + `agentica_core/insights.py` — do not hand-edit the table between the markers. Per-row
1–10 Utility/Safety scores from the original hand-authored audit are not regenerated (no live
source produces them; inventing numbers here would recreate exactly the kind of unfalsifiable
scoring this remediation plan retires) — narrative judgment calls now live in the prose sections
below the rosters instead of per-row.

<!-- GENERATED:ROSTER:BOW:START -->
| Metric Name | Group | Status | Skill | Command | Kind |
| :--- | :--- | :---: | :--- | :--- | :---: |
| **Avg_Session_Turns** | Activity | Graded | `insights` | `/insights` | advisory |
| **Complexity_Weighted_Throughput** | Activity | Observational | `insights` | `/insights` | advisory |
| **Error_Rate** | Activity | Graded | `investigate` | `/investigate` | advisory |
| **Estimated_Agent_Time_Saved** | Activity | Observational | `—` | `—` | no_route |
| **Latency_P95** | Activity | Graded | `investigate` | `/investigate` | advisory |
| **MCP_Smoke_Fails** | Activity | Graded | `mcp-setup` | `/mcp-setup` | auto_fix |
| **Session_Count** | Activity | Observational | `—` | `—` | no_route |
| **Tool_Calls** | Activity | Observational | `tool-diversity-audit` | `/tool-diversity-audit` | advisory |
| **Lesson_Graduation_Rate** | Agent Operation | Observational | `—` | `—` | no_route |
| **Agent_Process_Count** | Autonomic | Observational | `self-heal` | `/self-heal` | mis_route |
| **Mechanism_Liveness** | Autonomic | Observational | `—` | `—` | no_route |
| **Mechanism_Orphans** | Autonomic | Graded | `audit-mechanisms` | `/audit-mechanisms` | advisory |
| **Mitigation_Route_Validity** | Autonomic | Observational | `—` | `—` | no_route |
| **Remediation_Delta** | Autonomic | Graded | `insights` | `/insights` | advisory |
| **Self_Correction_Rate** | Autonomic | Observational | `—` | `—` | no_route |
| **Instrumentation_Coverage** | Coverage | Observational | `audit-mechanisms` | `/audit-mechanisms` | auto_fix |
| **Config_Drift_Rate** | Governance | Observational | `—` | `—` | no_route |
| **Governance_Pass_Rate** | Governance | Graded | `runtime-refactor-hardening` | `/runtime-refactor-hardening` | auto_fix |
<!-- GENERATED:ROSTER:BOW:END -->

---

## ⚔️ Pillar 2: Way of the Sword (Security & Policy Enforcement)
Sandbox boundaries, secret exposure, policy conformance, vulnerability remediation, and tripwire canaries.

Roster GENERATED from live payload + `insights.py` — see the Bow section above for the
regeneration command and why per-row Utility/Safety scores are not regenerated.

<!-- GENERATED:ROSTER:SWORD:START -->
| Metric Name | Group | Status | Skill | Command | Kind |
| :--- | :--- | :---: | :--- | :--- | :---: |
| **Gate_Canary_Fault** | Audit Trail | Observational | `canary-fault-diagnosis` | `/canary-fault-diagnosis` | advisory |
| **Boundary_Violations** | Code Security | Graded | `guard` | `/guard` | mis_route |
| **Secrets_Detected** | Code Security | Graded | `security-audit` | `/security-audit` | auto_fix |
| **Instrumentation_Coverage** | Coverage | Observational | `audit-mechanisms` | `/audit-mechanisms` | auto_fix |
| **Governance_Review_Findings** | Governance | Graded | `governance-review` | `/governance-review` | auto_fix |
| **Governance_Work_Volume** | Governance | Observational | `—` | `—` | no_route |
| **Kill_Chains_Disrupted** | Governance | Observational | `—` | `—` | no_route |
| **Kill_Chains_Open** | Governance | Graded | `guard` | `/guard` | mis_route |
| **Pending_Chain_Proposals** | Governance | Observational | `—` | `—` | no_route |
| **Rule_Violations** | Governance | Graded | `policy-enforcement-audit` | `/policy-enforcement-audit` | advisory |
| **Skill_Routing_Adherence** | Governance | Graded | `insights` | `/insights` | advisory |
| **Verifier_Falsifiability** | Governance | Graded | `insights` | `/insights` | advisory |
| **Deprecated_Deps** | Supply Chain | Graded | `pip-safe-upgrade` | `/pip-safe-upgrade` | auto_fix |
| **Open_CVEs** | Vulnerability | Graded | `pip-safe-upgrade` | `/pip-safe-upgrade` | auto_fix |
<!-- GENERATED:ROSTER:SWORD:END -->

---

## 🖌️ Pillar 3: Way of the Brush (Architecture & Cost Efficiency)
Financial spending, token density, local/cloud routing, subagent nesting, code simplifications, and repository hygiene.

Roster GENERATED from live payload + `insights.py` — see the Bow section above for the
regeneration command and why per-row Utility/Safety scores are not regenerated.

<!-- GENERATED:ROSTER:BRUSH:START -->
| Metric Name | Group | Status | Skill | Command | Kind |
| :--- | :--- | :---: | :--- | :--- | :---: |
| **Architecture_Scorecard_Grade** | Architecture | Graded | `runtime-refactor-hardening` | `/runtime-refactor-hardening` | auto_fix |
| **Hardcoded_Path_Incidents** | Code Health | Graded | `doctor` | `/doctor` | auto_fix |
| **Revision_Ratio** | Code Health | Observational | `simplify` | `/simplify` | advisory |
| **Root_Hygiene_Issues** | Code Health | Graded | `doctor` | `/doctor` | auto_fix |
| **Instrumentation_Coverage** | Coverage | Observational | `audit-mechanisms` | `/audit-mechanisms` | auto_fix |
| **Chain_Depth_Avg** | Orchestration | Graded | `subagent-audit` | `/subagent-audit` | auto_fix |
| **MCP_vs_CLI_Ratio** | Orchestration | Observational | `—` | `—` | no_route |
| **Subagent_Efficiency_Index** | Orchestration | Observational | `subagent-audit` | `/subagent-audit` | advisory |
| **Cache_Hit_Rate** | Token Efficiency | Observational | `—` | `—` | no_route |
| **Context_Cliff_Events** | Token Efficiency | Graded | `token-optimizer` | `/token-optimizer` | advisory |
| **Cost_Per_Outcome** | Token Efficiency | Observational | `—` | `—` | no_route |
| **Cost_Per_Task** | Token Efficiency | Observational | `cost-breakdown-audit` | `/cost-breakdown-audit` | advisory |
| **Estimated_Cost_Savings** | Token Efficiency | Observational | `—` | `—` | no_route |
| **Local_Routing_Share** | Token Efficiency | Graded | `model-selector` | `/model-selector` | mis_route |
| **Token_Execution_Density** | Token Efficiency | Graded | `token-optimizer` | `/token-optimizer` | advisory |
| **Token_Spend** | Token Efficiency | Observational | `token-optimizer` | `/token-optimizer` | advisory |
| **Total_Cost** | Token Efficiency | Observational | `cost-breakdown-audit` | `/cost-breakdown-audit` | advisory |
<!-- GENERATED:ROSTER:BRUSH:END -->

---

## 🎨 Pillar 4: Way of the Arts (Craft, UX, & Knowledge)
AI slop removal, user frustration signals, rework loop detection, doc-code parity, skill consolidation, and knowledge vault health.

Roster GENERATED from live payload + `insights.py` — see the Bow section above for the
regeneration command and why per-row Utility/Safety scores are not regenerated.

<!-- GENERATED:ROSTER:ARTS:START -->
| Metric Name | Group | Status | Skill | Command | Kind |
| :--- | :--- | :---: | :--- | :--- | :---: |
| **Instrumentation_Coverage** | Coverage | Observational | `audit-mechanisms` | `/audit-mechanisms` | auto_fix |
| **Craft_Improvements** | Craft | Observational | `—` | `—` | no_route |
| **Estimated_Human_Time_Saved** | Craft | Observational | `—` | `—` | no_route |
| **Skill_Conflicts** | Craft | Graded | `skill-consolidator` | `/skill-consolidator` | auto_fix |
| **Doc_Parity_Issues** | Docs | Graded | `wiki` | `/wiki` | advisory |
| **Frustration_Signals** | Interaction | Graded | `insights` | `/insights` | advisory |
| **Rework_Loops** | Interaction | Graded | `insights` | `/insights` | advisory |
| **Stop_Hook_Loops** | Interaction | Graded | `insights` | `/insights` | advisory |
| **Archive_Ratio** | Knowledge | Graded | `consolidate-memory` | `/consolidate-memory` | advisory |
| **Index_Drift** | Knowledge | Graded | `wiki` | `python3 Knowledge/okf/okf_tools.py index Knowledge/vault/me --root` | auto_fix |
| **Knowledge_Prompted** | Knowledge | Observational | `—` | `—` | no_route |
| **Knowledge_Staleness_Days** | Knowledge | Graded | `consolidate-memory` | `/consolidate-memory` | advisory |
| **OKF_Conformance** | Knowledge | Graded | `wiki` | `python3 Knowledge/okf/okf_tools.py validate Knowledge/vault --list 20` | advisory |
| **Orphan_Concepts** | Knowledge | Observational | `—` | `—` | no_route |
| **Raw_Pending** | Knowledge | Graded | `wiki` | `/wiki` | advisory |
| **Retrieval_Relevance** | Knowledge | Graded | `wiki` | `/wiki` | advisory |
| **Wiki_Article_Count** | Knowledge | Observational | `—` | `—` | no_route |
| **Wiki_Health_Score** | Knowledge | Observational | `wiki` | `/wiki` | auto_fix |
| **Wiki_Orphans** | Knowledge | Graded | `wiki` | `/wiki` | advisory |
| **Faithfulness_Score** | Output Quality | Graded | `insights` | `/insights` | advisory |
| **Refusal_Appropriateness** | Output Quality | Graded | `insights` | `/insights` | advisory |
| **Slop_Density** | Output Quality | Graded | `humanizer` | `/humanizer` | auto_fix |
| **Tool_Arg_Correctness** | Output Quality | Graded | `insights` | `/insights` | advisory |
| **Tool_Response_Utilization** | Output Quality | Graded | `insights` | `/insights` | advisory |
| **Tool_Selection_Accuracy** | Output Quality | Graded | `insights` | `/insights` | advisory |
| **Simplify_Age** | Process | Graded | `simplify` | `/simplify` | advisory |
<!-- GENERATED:ROSTER:ARTS:END -->

---

## ⚙️ Meta Group (Instrumentation)
Tracks the integrity of telemetry sensors and hooks.

**2026-08-01 correction:** `Instrumentation_Coverage` is not one global metric — the live registry
computes it once PER PILLAR (a "Coverage" group in each of Bow/Sword/Brush/Arts, reading that
pillar's own envelope-presence ratio). It now appears under its own pillar's generated roster
above instead of a single synthetic row here; there is nothing further to generate in this section.

---

## 🛠️ Summary of Metric De-Aggregation & Retiring Actions

1.  **Verifier_Failures (Pillar 1 - Bow):** Demoted to observational. The `Governance_Pass_Rate` remains the primary graded metric. This eliminates redundancy. Reflex engine must not trigger.
2.  **Gate_Canary_Fault (Pillar 2 - Sword):** Demoted to observational. The `Canary_Failures` metric remains the single source of truth for tripwire breach status. Reflex engine must not trigger.
3.  **Wiki_Health_Score (Pillar 4 - Arts):** Demoted to observational. The raw components (`Doc_Parity_Issues`, `Wiki_Orphans`, `Raw_Pending`) remain graded. This prevents double-penalizing the Arts score. Reflex engine must not trigger.

## ⚙️ Pending Deterministic Mechanism Wiring (Staged — Not Yet Applied)

As of 2026-06-28, `RONIN-MECHANISM-ROUTE-PLAN.md` (v2) is staged but not yet applied to `agentica_core/insights.py` or `api/src/reflex-engine.ts`. The following 7 metrics will shift from LLM-primary to deterministic-primary remediation once wired:

| Metric | Mechanism Script | Tier | Notes |
| :--- | :--- | :---: | :--- |
| `Deprecated_Deps` | `codebase_deps_audit.py` | Tier-1 | Detect-only; `pip_safe_upgrade.py` (Tier-2) applies fix |
| `Rule_Violations` | `policy_enforcement_audit.py` | Tier-1 | Full replacement of LLM skill for detect phase |
| `Subagent_Efficiency_Index` | `subagent_audit.py` | Tier-1 | Deterministic log analysis replaces LLM |
| `Canary_Failures` | `canary_fault_detect.py` | Tier-1 hybrid | Detect half only; repair stays LLM |
| `Vulnerability_MTTR` | `pip_safe_upgrade.py` | Tier-2 | Mutating; use with caution; 0% efficacy on LLM path |
| `Deprecated_Deps` | `pip_safe_upgrade.py` | Tier-2 | Runs after `codebase_deps_audit.py` confirms |
| `Skill_Conflicts` | `skill_consolidator.py` | Tier-2 hybrid | Detect only; merge judgment stays LLM |

**Blocked by:** multi-file edits to `insights.py`, `reflex-engine.ts`, and `bin/emit_event.py` plus server rebuild. Efficacy for detect-only Tier-1 mechanisms must be measured on verdict correctness, not score movement (they are read-only and will not flip `improved: true` in `skill_efficacy.json`).

---

## 🛠️ Appendix: Global config Agent Skills Analysis

The global agent configuration directory contains a set of standard, pre-installed agent skills. This section audits these skills, mapping them to the Governance dashboard metrics as alternative, out-of-the-box mitigations.

### 📋 Global Skills Matrix

| Skill Folder Name | Workspace Status | Target Metric Mapping | Utility Score (1-10) | Safety/Soundness (1-10) | Audit Notes & Recommendations |
| :--- | :---: | :--- | :---: | :---: | :--- |
| **arch-hygiene** | **Active** | `Root_Hygiene_Issues`, `Hardcoded_Path_Incidents` | **8 / 10** | **9 / 10** | Enforces path normalization and sweeps temp directories. Serves as a highly reliable, deterministic alternative to raw `/doctor` or `/guard` executions. |
| **runtime-refactor-hardening** | **Active** | `Architecture_Scorecard_Grade`, `Governance_Pass_Rate` | **9 / 10** | **8 / 10** | Hardens runtime entrypoints during refactoring. Employs pre/post validation checks to prevent breaking changes. |
| **token-optimizer** | **Active** | `Token_Execution_Density`, `Token_Spend` | **9 / 10** | **9 / 10** | Defines strict formatting and token compaction guidelines. Safe, declarative instructions that prevent LLM billing bloat. |
| **skill-discovery** | **Active** | `Skill_Conflicts`, `Skill_Promotions` | **8 / 10** | **9 / 10** | Scans registries for pre-existing modules before writing code, preventing duplicate/conflicting custom skills. |
| **skill-creator** | **Active** | `Skill_Promotions` | **8 / 10** | **8 / 10** | Automates skill generation templates. Extremely useful for promoting console scripts to permanent skills, reducing custom code variance. |
| **humanizer** | **Active** | `Slop_Density` | **9 / 10** | **9 / 10** | Deterministically strips conversational fluff and AI-isms from agent responses using safe regex and phrase blocks. |
| **axios-compromise** | **Active** | `Secrets_Detected`, `Deprecated_Deps`, `Vulnerability_MTTR` | **9 / 10** | **9 / 10** | Explicitly audits and sweeps lockfiles for compromised versions of the axios library. A vital security hardening tool. |
| **vertex-ai** / **vertex-deploy** / **vertex-inference** | **Inactive** | `Local_Routing_Share`, `Fallback_Recovery_Rate` | **7 / 10** | **8 / 10** | Manages custom model endpoints on Vertex AI. Inactive for this workspace unless the agent is configuring self-hosted model backends. |
| **clerk-setup** / **clerk-swift** / **clerk-testing** | **Inactive** | *None* | **1 / 10** (for governance) | **N/A** | Authentication integration blueprints for Clerk Auth. Unrelated to system governance metrics. |
| **valyu-data** | **Inactive** | *None* | **1 / 10** (for governance) | **N/A** | Dataset schema validation tool. Inactive for this workspace's telemetry structure. |

