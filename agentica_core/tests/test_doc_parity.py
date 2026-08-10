"""Tests for scouts/doc_parity._has_doc() — the module-level doc matching logic."""
from pathlib import Path
import pytest
from agentica_core.scouts import doc_parity


def _make_doc(docs_root: Path, content: str, name: str = "solution.md") -> Path:
    docs_root.mkdir(parents=True, exist_ok=True)
    p = docs_root / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _has_doc — with controlled DOCS_ROOT
# ---------------------------------------------------------------------------

def test_has_doc_returns_false_when_docs_root_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path / "nonexistent")
    assert doc_parity._has_doc("some_module.py") is False


def test_has_doc_matches_by_stem(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, "---\nmodule: reflex_engine\n---\n# Docs")
    assert doc_parity._has_doc("src/reflex_engine.py") is True


def test_has_doc_matches_case_insensitively(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, "---\nmodule: Reflex_Engine\n---\n# Docs")
    assert doc_parity._has_doc("src/reflex_engine.py") is True


def test_has_doc_matches_underscore_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, "---\nmodule: aggregate\n---\n# Docs")
    assert doc_parity._has_doc("agentica_core/aggregate.py") is True


def test_has_doc_returns_false_when_no_matching_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, "---\nmodule: something_else\n---\n# Docs")
    assert doc_parity._has_doc("src/reflex_engine.py") is False


def test_has_doc_skips_module_outside_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    # "module: reflex_engine" appears in the body, NOT in frontmatter — must not match
    _make_doc(tmp_path, "---\ntitle: docs\n---\n# Docs\nmodule: reflex_engine\n")
    assert doc_parity._has_doc("src/reflex_engine.py") is False


def test_has_doc_matches_quoted_value(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, '---\nmodule: "reflex_engine"\n---\n')
    assert doc_parity._has_doc("src/reflex_engine.py") is True


def test_has_doc_searches_recursively(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.md").write_text("---\nmodule: aggregate\n---\n# Docs", encoding="utf-8")
    assert doc_parity._has_doc("aggregate.py") is True


def test_has_doc_returns_false_for_empty_docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    assert doc_parity._has_doc("reflex_engine.py") is False


def test_has_doc_multiple_docs_only_one_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_parity, "DOCS_ROOT", tmp_path)
    _make_doc(tmp_path, "---\nmodule: aggregate\n---\n", name="agg.md")
    _make_doc(tmp_path, "---\nmodule: other_module\n---\n", name="other.md")
    assert doc_parity._has_doc("aggregate.py") is True
    assert doc_parity._has_doc("reflex_engine.py") is False


# --- no-data-looks-healthy sweep (2026-08-08) -------------------------------
# `doc_parity_issues: 0` is this metric's PERFECT score. Before this, an unreadable
# source produced exactly that: _changed_py_files() never checked git's return code,
# so a non-repo REPO (exit 128, empty stdout) yielded [] -> 0 stale -> flawless parity.
# Worse, scouts/__init__.py let that value OVERWRITE the real file-derived count.
# None is now the data-gap signal and the caller omits it.

def test_dead_repo_reports_none_not_a_perfect_score(monkeypatch):
    monkeypatch.setattr(doc_parity, "REPO", "/nonexistent-repo-path-for-this-test")
    result = doc_parity.run()
    assert result["doc_parity_issues"] is None, "an unreadable source must not read as 0"
    assert result["data_gap"]


def test_failed_git_command_reports_none(monkeypatch):
    """Exit 128 with empty stdout is the shape Global Anti-Pattern #1 warns about."""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)

    class _Failed:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(doc_parity.subprocess, "run", lambda *a, **k: _Failed())
    assert doc_parity._changed_py_files() is None


def test_empty_result_from_a_WORKING_repo_is_still_zero(monkeypatch):
    """The distinction that matters: ran-and-found-nothing is a real 0, not a gap."""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(doc_parity.subprocess, "run", lambda *a, **k: _Ok())
    assert doc_parity._changed_py_files() == []
    assert doc_parity.run()["doc_parity_issues"] == 0
