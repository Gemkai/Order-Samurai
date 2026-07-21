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
