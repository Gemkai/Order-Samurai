"""Acceptance tests for registry entries shared by export and public trees."""

import importlib.util
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "import_boundary_guard", Path(__file__).with_name("test_import_boundary.py")
)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


@pytest.fixture
def site():
    return {
        "file": "bin/probe.py",
        "symbol": "probe",
        "imports": ["external_probe"],
        "path_calls": ["sys.path.insert"],
        "target": "Optional external probe",
        "reason": "Use the probe when installed",
        "degrade": "Report the probe as unavailable",
        "visibility": "visible",
        "surface": "doctor probe warning",
    }


@pytest.fixture
def findings(site):
    return [
        guard.Finding(site["file"], site["symbol"], "import", "external_probe", 4),
        guard.Finding(site["file"], site["symbol"], "path", "sys.path.insert", 5),
    ]


@pytest.mark.parametrize(
    "marker,expected", [("absent", "export"), ("directory", "export"), ("file", "public")]
)
def test_tree_kind_requires_licensing_file(tmp_path, marker, expected):
    package = tmp_path / "agentica_core"
    package.mkdir()
    licensing = package / "licensing.py"
    if marker == "file":
        licensing.write_text("", encoding="utf-8")
    elif marker == "directory":
        licensing.mkdir()

    assert guard.tree_kind(tmp_path) == expected


@pytest.mark.parametrize(
    "scope,kind,present,expected",
    [
        ("shared", "export", True, None),
        ("shared", "export", False, "stale"),
        ("shared", "public", True, None),
        ("shared", "public", False, "stale"),
        ("export-only", "export", True, None),
        ("export-only", "export", False, "stale"),
        ("export-only", "public", True, "shared"),
        ("export-only", "public", False, None),
        ("public-only", "export", True, "shared"),
        ("public-only", "export", False, None),
        ("public-only", "public", True, None),
        ("public-only", "public", False, "stale"),
    ],
)
def test_registry_scope_matrix(site, findings, scope, kind, present, expected):
    site["scope"] = scope
    problems = guard.registry_problems(findings if present else [], [site], kind)

    if expected is None:
        assert problems == []
    else:
        assert any(expected in problem for problem in problems), problems
        if expected == "shared":
            assert len(problems) == 1, problems
            assert not any("unregistered" in problem for problem in problems), problems


@pytest.mark.parametrize("kind", ["export", "public"])
@pytest.mark.parametrize("present", [False, True])
def test_registry_default_scope_is_shared(site, findings, kind, present):
    problems = guard.registry_problems(findings if present else [], [site], tree_kind=kind)
    if present:
        assert problems == []
    else:
        assert any("stale" in problem for problem in problems), problems


def test_registry_default_tree_kind_is_export(site, findings):
    site["scope"] = "export-only"
    assert guard.registry_problems(findings, [site], "export") == []
    assert guard.registry_problems(findings, [site]) == []
    assert guard.registry_problems([], [site]) == guard.registry_problems([], [site], "export")
    site["scope"] = "public-only"
    assert guard.registry_problems([], [site]) == []
    problems = guard.registry_problems(findings, [site])
    assert len(problems) == 1 and "shared" in problems[0], problems


@pytest.mark.parametrize("kind", ["export", "public"])
def test_registry_rejects_invalid_scope(site, findings, kind):
    site["scope"] = "unknown"
    problems = guard.registry_problems(findings, [site], kind)
    assert any("scope" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "scope,home,other",
    [("export-only", "export", "public"), ("public-only", "public", "export")],
)
def test_one_registry_serves_both_tree_kinds(site, findings, scope, home, other):
    site["scope"] = scope
    sites = [site]
    assert guard.registry_problems(findings, sites, home) == []
    assert guard.registry_problems([], sites, other) == []

    problems = guard.registry_problems(findings, sites, other)
    assert len(problems) == 1 and "shared" in problems[0], problems
    assert not any("unregistered" in problem for problem in problems), problems


def test_export_only_entry_still_checks_stale_imports(site, findings):
    site.update(scope="export-only", imports=["external_probe", "removed_probe"])
    problems = guard.registry_problems(findings, [site], "export")
    assert any("stale imports" in problem for problem in problems), problems


@pytest.mark.parametrize("kind", ["export", "public"])
@pytest.mark.parametrize(
    "case,expected",
    [
        ("missing_field", "missing fields"),
        ("duplicate", "duplicate"),
        ("silent", "silent"),
        ("blank_visibility", "silent"),
        ("blank_surface", "silent"),
        ("stale_path_calls", "stale path_calls"),
        ("extra_import", "unregistered"),
        ("extra_path", "unregistered"),
    ],
)
def test_applicable_entries_keep_existing_checks(site, findings, kind, case, expected):
    site["scope"] = f"{kind}-only"
    sites = [site]
    if case == "missing_field":
        del site["reason"]
    elif case == "duplicate":
        sites.append(dict(site))
    elif case == "silent":
        site["visibility"] = "silent"
    elif case == "blank_visibility":
        site["visibility"] = ""
    elif case == "blank_surface":
        site["surface"] = " "
    elif case == "stale_path_calls":
        site["path_calls"].append("sys.path.append")
    elif case == "extra_import":
        findings.append(guard.Finding(site["file"], site["symbol"], "import", "another_probe", 6))
    elif case == "extra_path":
        findings.append(guard.Finding(site["file"], site["symbol"], "path", "sys.path.append", 7))

    problems = guard.registry_problems(findings, sites, kind)
    assert any(expected in problem for problem in problems), problems


@pytest.mark.parametrize("suffix", ["", "[param]"])
@pytest.mark.parametrize(
    "scope,kind",
    [("shared", "export"), ("shared", "public"),
     ("export-only", "export"), ("public-only", "public")],
)
def test_visibility_test_accepts_top_level_function(tmp_path, site, suffix, scope, kind):
    test_file = tmp_path / "tests" / "test_probe.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_warning():\n    pass\n", encoding="utf-8")
    site.update(scope=scope, visibility_test=f"tests/test_probe.py::test_warning{suffix}")

    assert guard.visibility_test_problems([site], tmp_path, kind) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(None, id="missing-file"),
        pytest.param("def test_other():\n    pass\n", id="missing-function"),
        pytest.param("# def test_warning():\n", id="comment-only"),
        pytest.param('text = "def test_warning():"\n', id="string-only"),
        pytest.param("def outer():\n    def test_warning():\n        pass\n", id="nested-function"),
        pytest.param("class Checks:\n    def test_warning(self):\n        pass\n", id="method-only"),
    ],
)
@pytest.mark.parametrize("kind", ["export", "public"])
def test_visibility_test_rejects_missing_top_level_function(tmp_path, site, source, kind):
    if source is not None:
        (tmp_path / "test_probe.py").write_text(source, encoding="utf-8")
    site["visibility_test"] = "test_probe.py::test_warning"

    problems = guard.visibility_test_problems([site], tmp_path, kind)
    assert any("visibility_test" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "scope,kind", [("export-only", "public"), ("public-only", "export")]
)
def test_visibility_test_skips_other_tree(tmp_path, site, scope, kind):
    site.update(scope=scope, visibility_test="missing.py::test_warning")
    assert guard.visibility_test_problems([site], tmp_path, kind) == []


@pytest.mark.parametrize("kind", ["export", "public"])
@pytest.mark.parametrize("field", [None, ""])
def test_visibility_test_is_optional(tmp_path, site, kind, field):
    if field is not None:
        site["visibility_test"] = field
    assert guard.visibility_test_problems([site], tmp_path, kind) == []


def test_visibility_test_default_tree_kind_is_export(tmp_path, site):
    site.update(scope="public-only", visibility_test="missing.py::test_warning")
    assert guard.visibility_test_problems([site], tmp_path) == []
    site["scope"] = "export-only"
    problems = guard.visibility_test_problems([site], tmp_path)
    assert any("visibility_test" in problem for problem in problems), problems
