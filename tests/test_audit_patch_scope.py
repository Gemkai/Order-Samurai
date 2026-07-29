"""Tests for the deterministic path-scope gate in audit_remediation_patch.py.

check_path_scope is the pre-LLM, non-prompt-injectable layer that rejects
patches touching the control plane, escaping via traversal/absolute paths, or
carrying binary hunks. These tests call it directly — no LLM, no network.
"""
import importlib.util
from pathlib import Path

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


def test_rejects_edit_to_gitignore_and_settings():
    assert arp.check_path_scope(_edit(".gitignore"))
    assert arp.check_path_scope(_edit("Governance/api/settings.json"))


def test_rejects_edit_to_hooks_and_subbundles():
    assert arp.check_path_scope(_edit("sub-bundles/claude/hooks/prompt_injection_guard.py"))
    assert arp.check_path_scope(_edit(".claude/settings.json"))


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
