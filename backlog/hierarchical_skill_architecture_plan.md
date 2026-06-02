# Implementation Plan: Order Samurai Hierarchical Skill Architecture

**Goal**: Transform the Order Samurai concepts into an executable "Skill Chaining" architecture, featuring a Master Controller, 4 Pillar Orchestrators, and linked autonomic Child Skills that operate on continuous metric-driven improvement loops.

**Constraints & Rules Followed**:
- Skills will be stored locally in the project root under `skills/` to isolate them to this workspace.
- The architecture must follow Progressive Disclosure (Master -> Orchestrator -> Child Skill -> Execution Script).
- Feedback loops must be quantified using the specific metrics defined in `autonomous_ronins.md`.

## Step-by-Step Execution Plan

### Step 1: Scaffold the Skill Hierarchy Directory
- Create a local `skills/` directory in the project root.
- Initialize the architecture backbone by creating the nested directory structure for the Master Controller, the 4 Orchestrators, and the initial batch of Child Skills.

### Step 2: Build the Master Controller Skill
- **File**: `skills/order-samurai/SKILL.md`
- **Role**: The single entrypoint. It receives a high-level command (e.g., "Run a full system audit"), evaluates the intent, and delegates tasks entirely to the 4 Pillar Orchestrators. It does *not* do any execution itself.

### Step 3: Build the 4 Pillar Orchestrators (Level 1)
- **Files**: 
  - `skills/way-of-the-bow/SKILL.md` (Operational Status)
  - `skills/way-of-the-sword/SKILL.md` (Security Integrity)
  - `skills/way-of-the-brush/SKILL.md` (Architecture Optimization)
  - `skills/way-of-the-arts/SKILL.md` (Project Performance)
- **Role**: State managers. They evaluate current metrics (e.g., checking `config/anti_drift_policy.json`) and call upon specific Level 2 Child Skills to perform the actual autonomic work.

### Step 4: Build the Autonomic Child Skills (Level 2)
- **Files**: Create the standalone execution skills based on the autonomic concepts we defined.
  - Examples: `skills/self-monitoring/SKILL.md`, `skills/self-patching/SKILL.md`, `skills/self-refactoring/SKILL.md`.
- **Role**: Hyper-focused "Hands." These skills contain the exact instructions to run `execution/doctor.py` or specific Python verifiers, returning raw output back to the Orchestrator.

### Step 5: Wire the "Improvement Loop" (Metrics Feedback)
- **Action**: Create a centralized metrics ledger (e.g., `reports/metrics_ledger.json`).
- **Logic**: Update the Child Skills to log their quantifiable results (e.g., `Patch Latency: 4s`, `Config Drift Rate: 0`) to this ledger. Update the Orchestrators to read this ledger before making decisions, ensuring they "learn" if a previous child skill execution failed or was too slow.

### Step 6: End-to-End Verification Test
- **Action**: Manually invoke the `/skill order-samurai` master controller and watch it autonomously trigger the `way-of-the-sword` orchestrator, which then triggers `self-patching`, logs the metric, and returns the result up the chain.
