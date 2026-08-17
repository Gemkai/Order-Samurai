"""Tests for the deterministic path-scope gate in audit_remediation_patch.py.

check_path_scope is the pre-LLM, non-prompt-injectable layer that rejects
patches touching the control plane, escaping via traversal/absolute paths, or
carrying binary hunks. These tests call it directly — no LLM, no network.
"""
import importlib.util
from pathlib import Path

import pytest

_CHECKER = Path(__file__).resolve().parents[1] / "execution" / "audit_remediation_patch.py"
_spec = importlib.util.spec_from_file_location("audit_remediation_patch", _CHECKER)
arp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arp)


def _edit(path):
    # A minimal well-formed unified-diff hunk editing `path`.
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )


# ---- rejections (the whole point of the gate) ----

def test_rejects_edit_to_the_checker_itself():
    p = "Governance/Order Samurai/execution/audit_remediation_patch.py"
    fails = arp.check_path_scope(_edit(p))
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_edit_to_reflex_engine():
    fails = arp.check_path_scope(_edit("Governance/api/src/reflex-engine.ts"))
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_edit_to_tracked_state_file():
    # The wargame's containment-escape vector: forge hitl_queue / flip skill_metadata.
    fails = arp.check_path_scope(_edit("Governance/Order Samurai/state/hitl_queue.json"))
    assert any("protected control-plane path" in f for f in fails)


# ---- 2026-08-16 audit: a patch must not rewrite what grades the next patch ----

@pytest.mark.parametrize("path", [
    # The policy a failing metric would want deleted. verify_root_hygiene.py reads
    # it via ROOT_HYGIENE_POLICY_PATH and its FAIL rows feed doctor's gating family,
    # so a patch dropping the offending rule turns the metric green with no change
    # to the underlying condition.
    "Governance/Order Samurai/config/root_hygiene_policy.json",
    "Governance/Order Samurai/config/claude_root_hygiene_policy.json",
])
def test_rejects_edit_to_policy_contract(path):
    fails = arp.check_path_scope(_edit(path))
    assert any("protected control-plane path" in f for f in fails), (
        f"{path} is an executable policy contract — a self-grading system must not "
        "be able to patch the rule it is being graded against"
    )


@pytest.mark.parametrize("path", [
    # doctor.py imports these verifiers directly; editing one to stop emitting its
    # FAIL is the same bypass as deleting the policy rule, one layer down.
    "Governance/Order Samurai/execution/verify_root_hygiene.py",
    "Governance/Order Samurai/execution/doctor.py",
    "Governance/Order Samurai/execution/score_claude_architecture.py",
])
def test_rejects_edit_to_a_verifier(path):
    fails = arp.check_path_scope(_edit(path))
    assert any("protected control-plane path" in f for f in fails), (
        f"{path} grades the patch; a patch that can silence its own verifier is "
        "not remediation"
    )


def test_still_allows_ordinary_non_grading_paths_under_the_same_tree():
    """Guard against over-widening: the two new patterns must not swallow docs,
    research or test files that legitimate remediation does touch."""
    for path in (
        "Governance/Order Samurai/Research/METRICS.md",
        "Governance/Order Samurai/docs/solutions/some-note.md",
        "Governance/Order Samurai/tests/test_something.py",
    ):
        assert arp.check_path_scope(_edit(path)) == [], path


def test_rejects_edit_to_gitignore_and_settings():
    assert arp.check_path_scope(_edit(".gitignore"))
    assert arp.check_path_scope(_edit("Governance/api/settings.json"))


def test_rejects_edit_to_hooks_and_subbundles():
    assert arp.check_path_scope(_edit("sub-bundles/claude/hooks/prompt_injection_guard.py"))
    assert arp.check_path_scope(_edit(".claude/settings.json"))


def test_rejects_edit_to_meditation_prompt():
    # Self-modification vector: a remediation patch rewriting the prompt that
    # governs how the NEXT remediation behaves. Only three named bin/ gates were
    # protected, so prompts/ was reachable with nothing catching it.
    p = "Governance/Order Samurai/prompts/meditation_cycle.md"
    fails = arp.check_path_scope(_edit(p))
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_edit_to_ronin_pillar_prompt():
    p = "Governance/Order Samurai/prompts/ronin_sword.md"
    fails = arp.check_path_scope(_edit(p))
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_path_traversal():
    fails = arp.check_path_scope(_edit("Governance/../../../etc/cron.d/evil"))
    assert any("traversal" in f for f in fails)


def test_rejects_absolute_path():
    patch = (
        "diff --git a/x b/x\n"
        "--- /dev/null\n"
        "+++ /etc/passwd\n"
        "@@ -0,0 +1 @@\n+pwned\n"
    )
    fails = arp.check_path_scope(patch)
    assert any("Absolute path" in f for f in fails)


def test_rejects_binary_patch():
    patch = (
        "diff --git a/state.bin b/state.bin\n"
        "index 0000..1111 100644\n"
        "GIT binary patch\n"
        "literal 4\n"
    )
    fails = arp.check_path_scope(patch)
    assert any("Binary patch" in f for f in fails)


def test_rejects_deletion_of_protected_file():
    # Deletion shows the protected path on the `--- a/` side, +++ is /dev/null.
    p = "Governance/Order Samurai/bin/bushido_check.py"
    patch = (
        f"diff --git a/{p} b/{p}\n"
        f"--- a/{p}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-guard\n"
    )
    fails = arp.check_path_scope(patch)
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_rename_into_protected_path():
    patch = (
        "diff --git a/harmless.py b/Governance/api/src/reflex-engine.ts\n"
        "similarity index 100%\n"
        "rename from harmless.py\n"
        "rename to Governance/api/src/reflex-engine.ts\n"
    )
    fails = arp.check_path_scope(patch)
    assert any("protected control-plane path" in f for f in fails)


# ---- must NOT reject legitimate remediations ----

def test_allows_normal_source_edit():
    # A real metric remediation editing ordinary kernel code must pass the gate.
    fails = arp.check_path_scope(_edit("Governance/agentica_core/insights.py"))
    assert fails == []


def test_allows_path_with_space_in_it():
    # "Order Samurai" contains a space — the authoritative +++/--- parse must
    # handle it; a docs edit under that tree (not state/) is allowed.
    p = "Governance/Order Samurai/Research/METRICS.md"
    assert arp.check_path_scope(_edit(p)) == []


# ---- non-standard diff prefixes must not bypass the gate ----

def _edit_with_prefix(path, src_prefix, dst_prefix):
    # A well-formed unified-diff hunk using an arbitrary --src-prefix/
    # --dst-prefix instead of git's conventional "a/"/"b/". `git apply`
    # (no -p flag) defaults to -p1: it strips exactly one leading path
    # component regardless of its literal name, so this is a realistic
    # patch shape, not a contrived one.
    return (
        f"diff --git {src_prefix}{path} {dst_prefix}{path}\n"
        f"--- {src_prefix}{path}\n"
        f"+++ {dst_prefix}{path}\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_rejects_protected_path_edit_under_nonstandard_diff_prefix():
    # The gate only strips a literal "a/"/"b/" prefix. A patch generated with
    # --src-prefix=x/ --dst-prefix=y/ still gets exactly one component
    # stripped by the real `git apply` call in reflex-engine.ts — so
    # "y/Governance/api/src/reflex-engine.ts" is written to
    # "Governance/api/src/reflex-engine.ts", the reflex engine itself, even
    # though the gate's ^-anchored pattern doesn't match the un-stripped
    # "y/..." path.
    patch = _edit_with_prefix("Governance/api/src/reflex-engine.ts", "x/", "y/")
    fails = arp.check_path_scope(patch)
    assert any("protected control-plane path" in f for f in fails)


def test_rejects_checker_edit_under_nonstandard_diff_prefix():
    p = "Governance/Order Samurai/execution/audit_remediation_patch.py"
    patch = _edit_with_prefix(p, "x/", "y/")
    fails = arp.check_path_scope(patch)
    assert any("protected control-plane path" in f for f in fails)


def test_allows_normal_edit_under_nonstandard_diff_prefix():
    # The alternate-stripped-path check must not create false positives for
    # an ordinary, unprotected file just because it uses a non-a/b prefix.
    patch = _edit_with_prefix("Governance/agentica_core/insights.py", "x/", "y/")
    assert arp.check_path_scope(patch) == []


# --- bin/ widening + allowlist carve-out (2026-08-09) -----------------------------

def test_rejects_edit_to_any_bin_script_not_just_the_three_named():
    """bin/ was widened from 3 named gates to the whole directory. Two of these are the
    reason: wiki_link.py carries the breach thresholds that decide whether a metric is
    failing (lowering them was the explicitly-rejected shortcut in the
    remediation-first-live-fire goal), and hitl_alerts.py IS the human escalation path.
    An agent able to edit either could suppress its own alarm."""
    for name in ("wiki_link.py", "hitl_alerts.py", "samurai", "meditation_overnight.sh",
                 "policy_enforcement_audit.py"):
        p = f"Governance/Order Samurai/bin/{name}"
        fails = arp.check_path_scope(_edit(p))
        assert any("protected control-plane path" in f for f in fails), p


def test_the_three_original_gates_are_still_rejected():
    """Regression guard: widening the pattern must not drop its original coverage."""
    for name in ("bushido_check", "remeasure_gate", "render_surface_env"):
        p = f"Governance/Order Samurai/bin/{name}.py"
        assert any("protected control-plane path" in f for f in arp.check_path_scope(_edit(p))), p


def test_allowlist_carve_out_permits_exactly_one_path(monkeypatch):
    """The carve-out must admit the ratified path and NOT its neighbours — exact-match
    only, so a future entry can never widen by accident (no globs)."""
    target = "Governance/Order Samurai/bin/wiki_compile.py"
    monkeypatch.setattr(arp, "REMEDIABLE_BIN_ALLOWLIST", frozenset({target}))
    assert arp.check_path_scope(_edit(target)) == []
    neighbour = "Governance/Order Samurai/bin/wiki_link.py"
    assert any("protected" in f for f in arp.check_path_scope(_edit(neighbour)))


def test_allowlist_is_empty_by_measurement():
    """Empty by evidence, not oversight: 0 of 165 exec_log rows ever touched bin/, and all
    50 files there are governance machinery. Seeding it with 'probably safe' guesses would
    hand back the self-modification surface the widening closes. If you add an entry, bring
    evidence (an exec_log row or a blocked patch) — and update this test deliberately."""
    assert arp.REMEDIABLE_BIN_ALLOWLIST == frozenset()


def test_rejection_message_names_the_allowlist():
    """A blocked-but-legitimate remediation must be self-diagnosing rather than a mystery."""
    fails = arp.check_path_scope(_edit("Governance/Order Samurai/bin/samurai"))
    assert any("REMEDIABLE_BIN_ALLOWLIST" in f for f in fails)
