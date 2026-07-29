---
title: "Claude verifier pack — 14 policy verifiers scoring the ~/.claude runtime from executable contracts"
date: "2026-07-19"
category: "docs/solutions/best-practices"
module: "verify_claude_runtime_coupling, verify_claude_doc_parity, verify_claude_doctor_truthfulness, verify_claude_generated_truth, verify_claude_hook_contract, verify_claude_mcp_contract, verify_claude_runtime_contract, verify_claude_runtime_portability, verify_claude_pack_integrity, verify_claude_path_authority, verify_claude_promotion_policy, verify_claude_root_hygiene, verify_claude_surface_governance, claude_runtime_target"
problem_type: "best_practice"
component: "tooling"
symptoms:
  - "The ~/.claude runtime had declared policies (hook contracts, MCP config rules, root hygiene) with nothing enforcing them"
  - "Architecture self-score rested on unmeasured categories"
root_cause: "design"
resolution_type: "code_fix"
severity: "medium"
related_components:
  - "config/claude_*.json policy contracts"
  - "execution/doctor.py (aggregates verifier results)"
  - "execution/score_claude_architecture.py (earned score)"
tags: [claude-pack, verifiers, policy-as-code, doctor, order-samurai]
---

# The claude verifier pack

One verifier per `config/claude_*.json` policy contract, all consumed by `doctor.py`
and rolled into the earned architecture score. The pack mirrors the repo-side
verifiers but targets the **~/.claude runtime** — the pairs are intentionally
separate surfaces, not duplicates.

## Contract shape

Each `verify_claude_<area>.py` reads its policy JSON (the executable contract),
scans the live runtime, and emits `[{status, name, detail}]` rows — `OK`/`WARN`/
`FAIL` — that `doctor.py` aggregates and `score_claude_architecture.py` grades.
A policy is only "real" when its verifier is a **gate, not a spectator**: FAIL
rows block the earned score from reaching 100.

`claude_runtime_target.py` is the shared resolver for *which* runtime the pack
scans (env override → symlink target → home default), so every verifier agrees
on the target even under worktrees or relocations.

## The allowlist pattern (runtime coupling)

`verify_claude_runtime_coupling` forbids live runtime files referencing
Antigravity-owned paths — EXCEPT allowlisted intentional bridges (e.g. the
retention reaper's documented cross-home GC). The allowlist entry carries a
`reason:` so an auditor can distinguish design from drift. When a coupling is
intentional, allowlist it with its reason — never weaken the scan.

## Lesson

Registering a policy file is step 1 of 3 (Anti-Pattern #5): the pack exists
because declared-but-unverified policy reads as governed while nothing enforces
it. The 2026-07-14 → 07-19 coupling-FAIL episode showed the failure mode: an
intentional coupling diagnosed as "should be allowlisted" stayed un-allowlisted
for 5 days and read as a standing FAIL until the allowlist landed.
