"""One automated, end-to-end fire-path test for sensei-cycle remediation.

Mines the scenario from `tests/test_sensei_e2e.md` (a manual, Windows-only
runbook — dead on this Mac: it hardcodes `C:\\Users\\example\\...` paths and can
only be run by hand against a live `claude` CLI + Order Samurai API). This file
is the first *automated pytest* covering any part of that path.

Fire path under test: payload breach -> eligibility -> worktree -> audit ->
pytest -> pending patch, with a stub skill spawn.

WHAT THIS TEST HONESTLY COVERS (real production functions, called directly —
no LLM, no network, no subprocess other than the sandboxed `main()` calls
below, no writes outside `tmp_path`):

  1. payload breach   -- agentica_core.reflexes.build_reflexes(): the real
                          tier-classification function that turns a metric
                          past its threshold into a CRITICAL/HIGH reflex on
                          the dispatch channel (the channel the reflex engine
                          may auto-fire from).
  4. audit             -- execution.audit_remediation_patch.check_path_scope()
                          and run_static_checks(): the same two gates
                          audit_remediation_patch.py's main() runs, in the
                          same order, before ever considering an LLM call.
                          Also exercises main() itself as a real subprocess
                          (mirrors reflex-engine.ts's spawnSync invocation)
                          for the REJECT branch only — a patch that fails
                          scope check never reaches the LLM branch, so this
                          is safe with zero network access.
  6. pending patch     -- agentica_core.bushido_engine.enqueue_hitl(): the
                          exact function reflex-engine.ts's embedded
                          HITL_ENQUEUE_PY delegates to when a validated patch
                          is ready for human review. Writes are redirected to
                          `tmp_path`; the item shape asserted matches what
                          `_enqueuePendingPatchHitl` constructs in production
                          (source="reflex_patch", skill/command/metric_id/
                          pillar/context).

WHAT THIS TEST DOES NOT COVER, AND WHY (verified against the source, not
assumed — see the sensei-fire-path exploration this test was written from):

  2. eligibility  -- ReflexEngine._isEligible() in Governance/api/src/
                     reflex-engine.ts is a `private` TypeScript instance
                     method operating on in-memory engine state (cooldowns,
                     noImprovement, verdicts). It is not exported and there
                     is no Python equivalent to call. Covering it honestly
                     needs a Node/vitest test (reflex-engine.ts already has
                     one, e.g. reflex-engine-grant-unpark-scope.test.ts),
                     not a Python one.
  3. worktree     -- reflex-engine.ts's `_execute()` shells out to
                     `git worktree add` via Node's child_process.spawnSync
                     directly (not through any Python script). Faking this
                     in Python would test "git worktree add works in
                     general," not the engine's actual orchestration around
                     it — that would overclaim coverage, so it is left out.
  5. pytest run   -- also inside reflex-engine.ts's `_execute()`
                     (`spawnSync(pythonBin, ['-m','pytest','tests/','-q'],
                     {cwd: worktreeSamuraiRoot})`), run against the *patched
                     worktree's* own test suite as a validation gate. A
                     pytest test cannot honestly assert that this specific
                     TypeScript-orchestrated invocation happens without
                     itself invoking reflex-engine.ts.
  7. skill spawn  -- reflex-engine.ts's `buildSkillSpawnArgs()` /
                     `child_process.spawn(this.claudeBin, ...)` runs the real
                     `claude` CLI. No real spawn happens here (no subprocess,
                     no network, no agent). `_stub_skill_spawn()` below stands
                     in for it by fabricating the one artifact everything
                     downstream actually consumes: a unified-diff patch
                     string keyed to the breach reflex's id.

Net: stages 1, 4, 6 are proven with real production code and real data
flowing between them (the breach reflex's id/command thread into the stub
patch and then into the WorkItem). Stages 2/3/5/7 are TypeScript-only or
require a live `claude` process; asserting anything about them from this file
would be pretending, not testing, so they are named here instead.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ORDER_SAMURAI_ROOT = Path(__file__).resolve().parents[1]
if str(_ORDER_SAMURAI_ROOT) not in sys.path:
    sys.path.insert(0, str(_ORDER_SAMURAI_ROOT))

from _layout import governance_root  # noqa: E402

_GOVERNANCE_ROOT = governance_root(__file__)
if str(_GOVERNANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE_ROOT))

from agentica_core import reflexes  # noqa: E402
from agentica_core.bushido_engine import (  # noqa: E402
    BlastRadius,
    Tier,
    WorkItem,
    enqueue_hitl,
)
from execution.audit_remediation_patch import (  # noqa: E402
    check_path_scope,
    run_static_checks,
)


def _seed_breach(tmp_path, monkeypatch):
    """Stage 1 — payload breach, via the real production classifier.

    A hermetic call: nudge/state paths point at nonexistent tmp_path files (no
    read of the real ~/.claude/nudges.json), and the reflex-engine "stuck"
    lookup is redirected off the real state/reflex_engine_state.json so the
    result cannot vary with this repo's live runtime state.
    """
    monkeypatch.setattr(
        reflexes, "_REFLEX_ENGINE_STATE", tmp_path / "state" / "reflex_engine_state.json"
    )
    pillars = {
        "arts": {"quality": {"Slop_Density": {"val": 42, "mitigation_command": "/humanizer"}}}
    }
    category_scores = {"arts": {"flags": [{"name": "Slop_Density", "grade": "F"}]}}
    dispatch, advisory = reflexes.build_reflexes(
        pillars,
        category_scores,
        by_project={},
        nudges_path=tmp_path / "nudges_missing.json",
        state_path=tmp_path / "nudge_state_missing.json",
    )
    assert advisory == []
    assert len(dispatch) == 1
    return dispatch[0]


def _stub_skill_spawn(reflex: dict) -> str:
    """Stage 7 stand-in — the ONLY thing downstream stages consume from the
    real `claude` skill spawn is the patch it produces. No subprocess, no
    agent, no network: this is a hand-built unified diff editing an ordinary
    (non-protected) source file, tagged with the breach reflex's id."""
    target = "Governance/agentica_core/insights.py"
    return (
        f"diff --git a/{target} b/{target}\n"
        f"--- a/{target}\n"
        f"+++ b/{target}\n"
        "@@ -1,1 +1,1 @@\n"
        "-old_line\n"
        f"+new_line  # remediation for {reflex['id']}\n"
    )


def test_sensei_fire_path_breach_to_pending_patch(tmp_path, monkeypatch):
    """The end-to-end chain: a real breach reflex feeds a stubbed skill-spawn
    patch through the real audit gates into a real pending-patch queue entry,
    with data (reflex id/command) threaded through at every step."""
    reflex = _seed_breach(tmp_path, monkeypatch)
    assert reflex["id"] == "metric:arts:Slop_Density"
    assert reflex["tier"] == "CRITICAL"
    assert reflex["status"] == "active"
    assert reflex["command"] == "/humanizer"

    patch = _stub_skill_spawn(reflex)

    # Stage 4 — audit, both deterministic gates, called exactly as
    # audit_remediation_patch.py's main() calls them (scope first, then static).
    assert check_path_scope(patch) == []
    assert run_static_checks(patch) == []

    # Stage 6 — pending patch, mirroring the WorkItem
    # reflex-engine.ts's _enqueuePendingPatchHitl() builds for a validated
    # propose-only patch (source="reflex_patch", not "reflex").
    work_item = WorkItem(
        skill="humanizer",
        source="reflex_patch",
        command=reflex["command"],
        blast_radius=BlastRadius.REPO,
        reversible=True,
        metric_id=reflex["id"],
        pillar="arts",
        context="Validated propose-only remediation patch awaiting review.",
    )
    queue_id = enqueue_hitl(work_item, Tier.HITL, tmp_path)

    queue_path = tmp_path / "state" / "hitl_queue.json"
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    items = [i for i in data["items"] if i["id"] == queue_id]
    assert len(items) == 1
    item = items[0]
    assert item["status"] == "pending"
    assert item["source"] == "reflex_patch"
    assert item["metric_id"] == "metric:arts:Slop_Density"
    assert item["pillar"] == "arts"
    assert item["command"] == "/humanizer"
    assert item["skill"] == "humanizer"


def test_sensei_fire_path_audit_blocks_self_modifying_patch(tmp_path):
    """Negative branch of stage 4: a patch that edits `state/` (the exact
    containment-escape vector this fire path exists to prevent) must be
    rejected by check_path_scope, and main() -- run as a real subprocess,
    exactly as reflex-engine.ts spawns it -- must exit 1 before ever reaching
    the LLM branch (no network call). A gate that has never been shown to
    reject anything is not evidence it works."""
    p = "Governance/Order Samurai/state/hitl_queue.json"
    malicious_patch = (
        f"diff --git a/{p} b/{p}\n"
        f"--- a/{p}\n"
        f"+++ b/{p}\n"
        "@@ -1,1 +1,1 @@\n"
        "-{}\n"
        '+{"items": []}\n'
    )
    scope_failures = check_path_scope(malicious_patch)
    assert any("protected control-plane path" in f for f in scope_failures)

    import os
    import subprocess

    script = _ORDER_SAMURAI_ROOT / "execution" / "audit_remediation_patch.py"
    patch_file = tmp_path / "remediation.patch"
    patch_file.write_text(malicious_patch, encoding="utf-8")
    env = {**os.environ, "GOVERNANCE_ROOT": str(_GOVERNANCE_ROOT)}
    proc = subprocess.run(
        [sys.executable, str(script), "--patch", str(patch_file)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "protected control-plane path" in proc.stdout

    # Nothing downstream ran: no pending-patch queue was ever created.
    assert not (tmp_path / "state" / "hitl_queue.json").exists()
