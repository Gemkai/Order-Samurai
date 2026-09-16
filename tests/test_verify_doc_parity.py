"""Regression coverage for verify_doc_parity.py's standalone-export doc
requirement (run_checks step 4).

Found 2026-08-31 (code review): the standalone-mode fallback self-filtered
declared_docs down to whichever originally-declared entries already existed
(`declared_docs = [d for d in declared_docs if (repo_root/d).is_file()] or
[...]`). That made the very next "missing required docs" check
tautologically unfailable for every survivor -- the filter had already
guaranteed each one's existence before the "is it missing" check ever ran.
As soon as ONE originally-declared doc happened to exist standalone, the
check could never fail again, no matter what else was missing.

Ground truth verified against the real repo before writing this fix: the
docs-move-with-runtime rule's real expectedArtifacts are exactly
["PROJECT.md", "RONIN_SPEC.md"] (config/anti_drift_policy.json), and both are
on bin/extract_public.py's own "Section 2, never ship" list -- their
standalone withdrawal is deterministic, not a maybe, which is why the fix
substitutes unconditionally rather than re-adding a different existence
check.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_doc_parity as vdp  # noqa: E402

# verify_doc_parity.py's own run_checks() does a LOCAL
# `from execution.claude_runtime_target import is_standalone_distribution` --
# a package-prefixed import, distinct in sys.modules from a bare
# `import claude_runtime_target`. Importing vdp above (which itself inserts
# the Order Samurai root onto sys.path and pulls in `execution.*` names at
# its own module top) registers the real `execution.claude_runtime_target`
# module; grab THAT object so monkeypatching it actually reaches the one
# run_checks() will resolve at call time, not a separate same-named module
# loaded under the bare top-level name.
import execution.claude_runtime_target as crt  # noqa: E402


def _policy_file(tmp_path: Path, expected_artifacts: list[str]) -> Path:
    """A minimal anti_drift_policy.json with exactly the real
    docs-move-with-runtime rule shape, declaring the given artifacts."""
    path = tmp_path / "anti_drift_policy.json"
    path.write_text(json.dumps({
        "rules": [{
            "id": vdp.DOC_PARITY_RULE_ID,
            "verifier": vdp.SELF_VERIFIER,
            "expectedArtifacts": expected_artifacts,
        }]
    }), encoding="utf-8")
    return path


def _run_standalone(monkeypatch, tmp_path: Path, *, files_present: list[str]) -> list[dict]:
    """Run run_checks() with is_standalone_distribution() forced True (the
    local import inside run_checks re-resolves this from the live module
    namespace, so patching claude_runtime_target directly is what's needed --
    patching vdp itself would miss it, since it's never a module-level name
    there) against a synthetic repo_root containing only `files_present`."""
    monkeypatch.setattr(crt, "is_standalone_distribution", lambda: True)
    policy = _policy_file(tmp_path, ["PROJECT.md", "RONIN_SPEC.md"])
    monkeypatch.setattr(vdp, "ANTI_DRIFT_POLICY_PATH", policy)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    for name in files_present:
        (repo_root / name).write_text("content", encoding="utf-8")

    return vdp.run_checks(repo_root=repo_root)


def _status_for(results: list[dict], label: str) -> dict | None:
    return next((r for r in results if r["label"] == label), None)


def test_standalone_with_neither_public_doc_present_fails(monkeypatch, tmp_path):
    """Neither PROJECT.md/RONIN_SPEC.md (withdrawn by design) nor the public
    stand-in README.md/AGENTS.md exist -- must FAIL, not pass by omission."""
    results = _run_standalone(monkeypatch, tmp_path, files_present=[])
    row = _status_for(results, "doc-parity.required-docs")
    assert row is not None
    assert row["status"] == "FAIL"
    assert "README.md" in row["detail"] and "AGENTS.md" in row["detail"]


def test_standalone_with_both_public_docs_present_passes(monkeypatch, tmp_path):
    results = _run_standalone(monkeypatch, tmp_path, files_present=["README.md", "AGENTS.md"])
    row = _status_for(results, "doc-parity.required-docs")
    assert row is not None
    assert row["status"] == "OK"


def test_standalone_with_only_one_of_the_two_actually_required_docs_fails(monkeypatch, tmp_path):
    """README.md present, AGENTS.md not -- the real (post-substitution) pair is
    not fully satisfied. Distinct from the withdrawn-internal-doc scenarios
    above: this tests the substituted requirement's OWN completeness, not the
    old tautology's blind spot."""
    results = _run_standalone(monkeypatch, tmp_path, files_present=["README.md"])
    row = _status_for(results, "doc-parity.required-docs")
    assert row is not None
    assert row["status"] == "FAIL"
    assert "AGENTS.md" in row["detail"]


def test_standalone_catches_a_partially_missing_public_doc_the_old_tautology_missed(
    monkeypatch, tmp_path
):
    """The exact regression proof: one of the ORIGINALLY declared internal
    docs (PROJECT.md) happens to be present -- synthetic, but this is
    precisely the shape of input the old self-filter mishandled: filtering
    declared_docs down to ["PROJECT.md"] because it survived the existence
    check, then finding it "not missing" (of course not -- the filter already
    proved it exists), permanently silencing the check for as long as any one
    original doc happened to be around. Neither README.md nor AGENTS.md (the
    REAL standalone requirement) exists here -- must still FAIL."""
    results = _run_standalone(monkeypatch, tmp_path, files_present=["PROJECT.md"])
    row = _status_for(results, "doc-parity.required-docs")
    assert row is not None
    assert row["status"] == "FAIL", (
        "PROJECT.md existing must not suppress the real standalone requirement "
        "(README.md/AGENTS.md) -- this is the tautology the old code had"
    )
    assert "README.md" in row["detail"] or "AGENTS.md" in row["detail"]


def test_standalone_export_does_not_warn_on_the_withdrawn_project_md_principle(
    monkeypatch, tmp_path
):
    """PROJECT.md carries the 'Docs Move With Runtime' principle marker, but
    PROJECT.md is itself on bin/extract_public.py's deterministic "never ship"
    list for a standalone export (see this file's docstring) -- its absence
    there is by design, not drift. Step 6 (doc-parity.principle) must not
    treat that deliberate withdrawal as a contract violation: a fully
    compliant standalone export (README.md + AGENTS.md present, PROJECT.md
    correctly absent) must not WARN on the principle check."""
    results = _run_standalone(monkeypatch, tmp_path, files_present=["README.md", "AGENTS.md"])
    row = _status_for(results, "doc-parity.principle")
    assert row is not None
    assert row["status"] != "WARN", (
        "standalone export must not be penalized for PROJECT.md's deterministic, "
        "by-design absence -- the principle check does not apply when the doc "
        "that carries it was never meant to ship"
    )


def test_non_standalone_mode_is_unaffected_and_still_requires_the_declared_docs(
    monkeypatch, tmp_path
):
    """Sanity check: outside standalone mode, the original declared_docs
    (PROJECT.md/RONIN_SPEC.md) are still the real requirement, untouched by
    this fix's standalone-only branch."""
    monkeypatch.setattr(crt, "is_standalone_distribution", lambda: False)
    policy = _policy_file(tmp_path, ["PROJECT.md", "RONIN_SPEC.md"])
    monkeypatch.setattr(vdp, "ANTI_DRIFT_POLICY_PATH", policy)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "PROJECT.md").write_text("content", encoding="utf-8")
    # RONIN_SPEC.md deliberately absent.

    results = vdp.run_checks(repo_root=repo_root)
    row = _status_for(results, "doc-parity.required-docs")
    assert row is not None
    assert row["status"] == "FAIL"
    assert "RONIN_SPEC.md" in row["detail"]
