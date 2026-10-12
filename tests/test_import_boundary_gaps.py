"""Acceptance coverage for shipped membership and fail-closed import scanning."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "import_boundary_gaps_guard", Path(__file__).with_name("test_import_boundary.py")
)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)


@pytest.fixture(scope="module")
def template():
    # Read the production guard once; each case gets an independent temporary tree.
    return {
        "tests/test_import_boundary.py": Path(__file__).with_name(
            "test_import_boundary.py"
        ).read_text(encoding="utf-8"),
        "agentica_core/__init__.py": "",
        "bin/inside.py": "",
        "config/out_of_package_imports.json": '{"sites": []}\n',
    }


def _write(root, rel, text=""):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(key, raising=False)


def _git(root, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false",
         "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=30,
    )


def _marker(root):
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                   if p.is_file() and p.name != ".export-tree")
    _write(root, ".export-tree", "# Shipped files\n\n" + "\n".join(files) + "\n")


@pytest.fixture
def make_tree(tmp_path, template):
    def make(layout):
        root = tmp_path / "tree"
        for rel, source in template.items():
            _write(root, rel, source)
        if layout in {"git", "worktree"}:
            _write(root, "agentica_core/licensing.py")
            _git(root, "init")
            _git(root, "add", ".")
            _git(root, "commit", "-m", "fixture")
            if layout == "worktree":
                linked = tmp_path / "linked"
                _git(root, "worktree", "add", "--detach", str(linked), "HEAD")
                root = linked
                assert (root / ".git").is_file()
        elif layout == "export-tree":
            _marker(root)
        return root
    return make


def _production(root, expected, *fragments):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_import_boundary.py::"
         "test_every_out_of_package_site_is_registered_and_visible"],
        cwd=root, env=os.environ.copy(), capture_output=True, text=True, timeout=60,
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected, output
    for fragment in fragments:
        assert fragment in output, output


# GREEN: clean baselines, including the linked-worktree .git-file layout.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree", "disk"])
def test_production_clean_tree(make_tree, layout):
    _production(make_tree(layout), 0)


# RED tracked / GREEN untracked: dot directories are shipped only by index membership.
@pytest.mark.parametrize("layout", ["git", "worktree"])
@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_production_git_dot_directory(make_tree, layout, tracked):
    root = make_tree(layout)
    _write(root, ".hooks/evil.py", "import not_shipped_anywhere_xyz\n")
    if tracked:
        _git(root, "add", ".hooks/evil.py")
        _production(root, 1, ".hooks/evil.py", "unregistered", "not_shipped_anywhere_xyz")
    else:
        _production(root, 0)


# RED tracked / GREEN untracked: inspect executable .pth files before the Python filter.
@pytest.mark.parametrize("layout", ["git", "worktree"])
@pytest.mark.parametrize("rel", ["x.pth", "lib/site/y.pth"])
@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_production_git_pth(make_tree, layout, rel, tracked):
    root = make_tree(layout)
    _write(root, rel, "import not_shipped_anywhere_xyz\n")
    if tracked:
        _git(root, "add", rel)
        _production(root, 1, rel)
    else:
        _production(root, 0)


# RED: scratch cannot satisfy a shipped import, whether it looks like a module or package.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree"])
@pytest.mark.parametrize("scratch", ["bin/hook_registry.py", "bin/hook_registry/__init__.py"])
def test_production_unshipped_module_cannot_resolve(make_tree, layout, scratch):
    root = make_tree(layout)
    _write(root, "bin/uses.py", "import hook_registry\n")
    if layout == "export-tree":
        _marker(root)
    else:
        _git(root, "add", "bin/uses.py")
    _write(root, scratch)
    _production(root, 1, "bin/uses.py", "unregistered", "hook_registry")


# GREEN today (syntax is currently dropped); stays green when scratch is excluded.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree"])
def test_production_unshipped_invalid_scratch_is_ignored(make_tree, layout):
    root = make_tree(layout)
    _write(root, "scratch.py", "def broken(:\n")
    _write(root, ".tmp/run/huge.py", "def broken(:\n")
    _production(root, 0)


# RED: a valid scratch import distinguishes membership filtering from syntax dropping.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree"])
def test_production_unshipped_valid_scratch_is_ignored(make_tree, layout):
    root = make_tree(layout)
    _write(root, "scratch.py", "import not_shipped_anywhere_xyz\n")
    _production(root, 0)


# RED: parse errors in shipped Python must reach the production assertion.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree"])
def test_production_shipped_syntax_error(make_tree, layout):
    root = make_tree(layout)
    _write(root, "bin/broken.py", "def broken(:\n")
    if layout == "export-tree":
        _marker(root)
    else:
        _git(root, "add", "bin/broken.py")
    _production(root, 1, "bin/broken.py:1")


# RED: a marker cannot override a Git index, even when the marker is untracked.
@pytest.mark.parametrize("layout", ["git", "worktree"])
@pytest.mark.parametrize("tracked", [True, False], ids=["tracked", "untracked"])
def test_production_marker_in_git_is_rejected(make_tree, layout, tracked):
    root = make_tree(layout)
    _write(root, ".export-tree", "bin/inside.py\n")
    if tracked:
        _git(root, "add", ".export-tree")
    _production(root, 1, ".export-tree")


# RED: export markers include dot directories and non-Python executable loaders.
@pytest.mark.parametrize("rel", ["x.pth", ".hooks/evil.py"])
def test_production_export_listed_reach_out(make_tree, rel):
    root = make_tree("export-tree")
    _write(root, rel, "import not_shipped_anywhere_xyz\n")
    _marker(root)
    _production(root, 1, rel)


# RED: invalid manifest entries must be diagnosed without escaping the temporary tree.
@pytest.mark.parametrize("rel", ["bin/missing.py", "/opt/external.py", "../escape.py"])
def test_production_export_invalid_marker_entry(make_tree, rel):
    root = make_tree("export-tree")
    with (root / ".export-tree").open("a", encoding="utf-8") as marker:
        marker.write(rel + "\n")
    _production(root, 1, rel)


# GREEN non-dot import + dot exclusion; RED .pth. This pins the documented disk gap.
@pytest.mark.parametrize("rel,code", [("bin/evil.py", 1), (".hooks/evil.py", 0), ("x.pth", 1)])
def test_production_disk_boundary(make_tree, rel, code):
    root = make_tree("disk")
    _write(root, rel, "import not_shipped_anywhere_xyz\n")
    _production(root, code, *([rel] if code else []))


# GREEN: fixture data and bytecode directories stay outside the Python scan.
@pytest.mark.parametrize("layout", ["git", "worktree", "export-tree", "disk"])
def test_production_fixture_and_bytecode_directories_are_excluded(make_tree, layout):
    root = make_tree(layout)
    for rel in ("tests/falsifiability_fixtures/bad.py", "bin/__pycache__/bad.py"):
        _write(root, rel, "import not_shipped_anywhere_xyz\n")
    if layout in {"git", "worktree"}:
        _git(root, "add", ".")
    elif layout == "export-tree":
        _marker(root)
    _production(root, 0)


# RED: the new API reports the source, complete membership, and clean diagnostics.
@pytest.mark.parametrize("layout,source", [
    ("git", "git"), ("worktree", "git"), ("export-tree", "export-tree"), ("disk", "disk"),
])
def test_shipped_files_source_and_membership(make_tree, template, layout, source):
    root = make_tree(layout)
    _write(root, ".hooks/tracked.py")
    _write(root, "data.txt", "data\n")
    if layout in {"git", "worktree"}:
        _git(root, "add", ".hooks/tracked.py", "data.txt")
    elif layout == "export-tree":
        _marker(root)
    _write(root, "scratch.py")
    result = guard.shipped_files(root)
    assert result.source == source
    assert result.problems == []
    assert isinstance(result.files, set)
    assert set(template) | {"data.txt"} <= result.files
    assert (".hooks/tracked.py" in result.files) == (layout != "disk")
    assert ("scratch.py" in result.files) == (layout == "disk")


@pytest.fixture
def fake_tree(tmp_path):
    root = tmp_path / "fake"
    for rel in ("bin/inside_module.py", "bin/y.py", "bin/tool.py", "execution/__init__.py",
                "execution/doctor.py", "pkg/__init__.py", "pkg/sibling.py"):
        _write(root, rel)
    return root


IMPORTS = ("import importlib.util, importlib.machinery, runpy, sys, os, subprocess\n"
           "from pathlib import Path\n")


def _scan(fake_tree, source):
    return guard.scan_source(IMPORTS + source, "bin/x.py", fake_tree)


def _one_path(findings, call, symbol="<module>", why="unresolved"):
    assert len(findings) == 1, findings
    finding = findings[0]
    assert (finding.file, finding.kind, finding.name, finding.symbol) == (
        "bin/x.py", "path", call, symbol,
    )
    if why == "unresolved":
        assert finding.why.startswith("unresolved"), finding
    else:
        assert finding.why == why


# RED: unknown arguments must fail closed at their canonical loader call and scope.
@pytest.mark.parametrize("call,expression", [
    ("importlib.util.spec_from_file_location", "importlib.util.spec_from_file_location('x', param)"),
    ("runpy.run_path", "runpy.run_path(param)"),
    ("importlib.machinery.SourceFileLoader", "importlib.machinery.SourceFileLoader('x', param)"),
    ("sys.path.insert", "sys.path.insert(0, param)"),
    ("importlib.import_module", "importlib.import_module(param)"),
    ("__import__", "__import__(param)"),
], ids=["spec", "run-path", "source-loader", "sys-path", "import-module", "dunder-import"])
def test_unresolved_loader(fake_tree, call, expression):
    _one_path(_scan(fake_tree, f"def f(param):\n    {expression}\n"), call, "f")


# RED withheld binding; GREEN stdlib binding and existing literal behavior.
@pytest.mark.parametrize("source,expected", [
    ('MOD = "pkg.withheld"\nimportlib.import_module(MOD)\n', [("import", "pkg.withheld")]),
    ('MOD = "json"\nimportlib.import_module(MOD)\n', []),
    ('importlib.import_module("pkg.withheld")\n', [("import", "pkg.withheld")]),
    ('importlib.import_module("pkg.sibling")\n', []),
    ('__import__("json")\n', []),
], ids=["bound-withheld", "bound-stdlib", "literal-withheld", "literal-in-tree", "literal-stdlib"])
def test_dynamic_import_binding(fake_tree, source, expected):
    assert [(f.kind, f.name) for f in _scan(fake_tree, source)] == expected


# RED: environment subscripts and overrides without a default need provenance.
@pytest.mark.parametrize("expression,why", [
    ('os.environ["HOME"]', "env HOME"),
    ('os.environ["USERPROFILE"]', "env USERPROFILE"),
    ('os.environ["X"]', "unresolved"),
    ('os.environ.get("X")', "unresolved"),
    ('os.getenv("X")', "unresolved"),
], ids=["home", "userprofile", "other-subscript", "get", "getenv"])
def test_environment_loader(fake_tree, expression, why):
    _one_path(_scan(fake_tree, f"sys.path.insert(0, {expression})\n"), "sys.path.insert", why=why)


# GREEN: in-tree path transformations and an in-code default remain accepted.
@pytest.mark.parametrize("source", [
    'sys.path.insert(0, os.environ.get("X", str(Path(__file__).resolve().parent)))',
    'importlib.util.spec_from_file_location("m", Path(__file__).with_name("y.py"))',
    'importlib.util.spec_from_file_location("m", Path(__file__).resolve().parent / "y.py")',
    'importlib.util.spec_from_file_location("m", Path(__file__).with_suffix(".py"))',
], ids=["env-default", "with-name", "parent", "with-suffix"])
def test_in_tree_loaders_remain_clean(fake_tree, source):
    assert _scan(fake_tree, source) == []


def _site(call, **fields):
    return {"file": "bin/x.py", "symbol": "<module>", "imports": [],
            "path_calls": [call], "target": "Optional loader", "reason": "Load a helper",
            "degrade": "Report unavailable", "visibility": "visible",
            "surface": "doctor warning", **fields}


# RED: explicit unresolved provenance is required and cannot outlive unresolved findings.
@pytest.mark.parametrize("case", ["declared", "missing-provenance", "stale-provenance", "unregistered"])
def test_unresolved_registry_provenance(fake_tree, case):
    call = "runpy.run_path"
    expression = 'runpy.run_path(Path.home() / "x.py")' if case == "stale-provenance" else "runpy.run_path(param)"
    findings = _scan(fake_tree, expression)
    site = _site(call)
    if case in {"declared", "stale-provenance"}:
        site["provenance"] = "unresolved"
    problems = guard.registry_problems(findings, [] if case == "unregistered" else [site])
    if case == "declared":
        assert problems == []
    else:
        expected = "unregistered" if case == "unregistered" else "provenance"
        assert any(expected in problem for problem in problems), problems


# RED: all Python subprocess sinks and argv forms must report the canonical call.
@pytest.mark.parametrize("source,call", [
    *[(f'subprocess.{name}([sys.executable, str(Path.home() / "x.py")])', f"subprocess.{name}")
      for name in ("run", "call", "check_call", "check_output", "Popen")],
    ('import subprocess as sp\nsp.run([sys.executable, str(Path.home() / "x.py")])', "subprocess.run"),
    ('from subprocess import run as r\nr([sys.executable, str(Path.home() / "x.py")])', "subprocess.run"),
    ('cmd = [sys.executable, str(Path.home() / "x.py")]\nsubprocess.run(cmd)', "subprocess.run"),
    ('cmd = (sys.executable, str(Path.home() / "x.py"))\nsubprocess.run(cmd)', "subprocess.run"),
    ('subprocess.run(args=[sys.executable, str(Path.home() / "x.py")])', "subprocess.run"),
    ('subprocess.run(["python3", str(Path.home() / "x.py")])', "subprocess.run"),
    ('subprocess.run(["/opt/bin/python3.12", str(Path.home() / "x.py")])', "subprocess.run"),
    ('subprocess.run(["python", str(Path.home() / "x.py")])', "subprocess.run"),
    ('subprocess.run([sys.executable, "-u", "-I", "-B", str(Path.home() / "x.py")])', "subprocess.run"),
], ids=["run", "call", "check-call", "check-output", "popen", "module-alias", "function-alias",
        "bound-list", "bound-tuple", "args-keyword", "python3", "versioned-python", "python", "flags"])
def test_python_subprocess_external_script(fake_tree, source, call):
    _one_path(_scan(fake_tree, source), call, why="Path.home()")


# RED: unknown script/module/code operands all belong to the calling function.
@pytest.mark.parametrize("argv", [
    "[sys.executable, param]", "[sys.executable, '-m', param]", "[sys.executable, '-c', param]",
], ids=["script", "module", "code"])
def test_python_subprocess_unresolved(fake_tree, argv):
    _one_path(_scan(fake_tree, f"def f(param):\n    subprocess.run({argv})"), "subprocess.run", "f")


# RED: literal -m and -c imports are checked in the calling symbol.
@pytest.mark.parametrize("argv,target", [
    ('[sys.executable, "-m", "withheld.mod"]', "withheld.mod"),
    ('[sys.executable, "-c", "import withheld_mod"]', "withheld_mod"),
], ids=["module", "code"])
def test_python_subprocess_imports(fake_tree, argv, target):
    findings = _scan(fake_tree, f"def f():\n    subprocess.run({argv})")
    assert [(f.kind, f.name, f.symbol) for f in findings] == [("import", target, "f")]


# GREEN: supported in-tree loads and the explicitly uninspected subprocess boundary.
@pytest.mark.parametrize("source", [
    'subprocess.run([sys.executable, "-m", "pytest"])',
    'subprocess.run([sys.executable, "-m", "json.tool"])',
    'subprocess.run([sys.executable, "-m", "pkg.sibling"])',
    'subprocess.run([sys.executable, "-c", "import json"])',
    'subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "tool.py")])',
    'subprocess.run(["git", "status"])',
    'subprocess.run([str(Path.home() / "x.sh")])',
    'subprocess.run(f"python {Path.home()}/x.py", shell=True)',
    'def f(argv_param):\n    subprocess.run(argv_param)',
], ids=["pytest", "stdlib-module", "in-tree-module", "stdlib-code", "in-tree-script",
        "git", "shell-script", "shell-true", "unknown-argv"])
def test_python_subprocess_clean_and_boundary(fake_tree, source):
    assert _scan(fake_tree, source) == []


# RED: subprocess findings participate in the ordinary path-call registry.
@pytest.mark.parametrize("registered", [True, False], ids=["registered", "unregistered"])
def test_python_subprocess_registry(fake_tree, registered):
    findings = _scan(fake_tree, 'subprocess.run([sys.executable, str(Path.home() / "x.py")])')
    problems = guard.registry_problems(findings, [_site("subprocess.run")] if registered else [])
    if registered:
        assert problems == []
    else:
        assert any("unregistered" in problem for problem in problems), problems


# RED: syntax errors are findings and no registry declaration may suppress them.
def test_syntax_error_is_a_parse_finding(fake_tree):
    findings = guard.scan_source("def broken(:\n", "bin/broken.py", fake_tree)
    assert len(findings) == 1, findings
    finding = findings[0]
    assert (finding.kind, finding.file, finding.line) == ("parse", "bin/broken.py", 1)


@pytest.mark.parametrize("registered", [False, True], ids=["unregistered", "registered"])
def test_syntax_error_cannot_be_registered_away(fake_tree, registered):
    findings = guard.scan_source("def broken(:\n", "bin/broken.py", fake_tree)
    sites = [_site("", file="bin/broken.py", path_calls=[], provenance="unresolved")] if registered else []
    problems = guard.registry_problems(findings, sites)
    assert any("bin/broken.py:1" in problem for problem in problems), problems


# F1 RED: loader keywords and unpacked arguments must reach the same checks.
@pytest.mark.parametrize("source,call,symbol,why", [
    ('importlib.util.spec_from_file_location("x", location=Path.home() / "o.py")',
     "importlib.util.spec_from_file_location", "<module>", "Path.home()"),
    ('def f(param):\n    runpy.run_path(path_name=param)',
     "runpy.run_path", "f", "unresolved"),
    ('def f(param):\n    importlib.machinery.SourceFileLoader("x", path=param)',
     "importlib.machinery.SourceFileLoader", "f", "unresolved"),
    ('def f(param):\n    __import__(name=param)', "__import__", "f", "unresolved"),
    ('runpy.run_path(*paths)', "runpy.run_path", "<module>", "unresolved"),
    ('importlib.util.spec_from_file_location("x", **kw)',
     "importlib.util.spec_from_file_location", "<module>", "unresolved"),
    ('subprocess.Popen(["tool", str(Path.home() / "x.py")], executable=sys.executable)',
     "subprocess.Popen", "<module>", "Path.home()"),
], ids=["spec-location", "run-path-name", "source-loader-path", "import-name",
        "star-args", "star-keywords", "python-executable"])
def test_f1_loader_arguments(fake_tree, source, call, symbol, why):
    _one_path(_scan(fake_tree, source), call, symbol, why)


def test_f1_import_module_keyword(fake_tree):
    findings = _scan(fake_tree, 'importlib.import_module(name="unshipped_module")')
    assert [(f.kind, f.name) for f in findings] == [("import", "unshipped_module")]


# F2 RED: Python options may contain both the option and its operand.
@pytest.mark.parametrize("argv", [
    '[sys.executable, "-cimport unshipped_module"]',
    '[sys.executable, "-munshipped_module"]',
    '[sys.executable, "-Im", "unshipped_module"]',
    '[sys.executable, "-Ic", "import unshipped_module"]',
], ids=["attached-code", "attached-module", "combined-module", "combined-code"])
def test_f2_python_option_imports(fake_tree, argv):
    findings = _scan(fake_tree, f"subprocess.run({argv})")
    assert [(f.kind, f.name) for f in findings] == [("import", "unshipped_module")]


@pytest.mark.parametrize("options", ['"-Wignore"', '"-X", "dev"'], ids=["warning", "xoption"])
def test_f2_python_option_script(fake_tree, options):
    source = f'subprocess.run([sys.executable, {options}, str(Path.home() / "x.py")])'
    _one_path(_scan(fake_tree, source), "subprocess.run", why="Path.home()")


# GREEN: combined flags still accept a standard-library module.
def test_f2_combined_stdlib_module_green(fake_tree):
    assert _scan(fake_tree, 'subprocess.run([sys.executable, "-Im", "json.tool"])') == []


# F3 RED: select the bound tuple element and retain differing branch values.
def test_f3_tuple_selected_path(fake_tree):
    source = ('a, b = Path(__file__), Path(__file__).parents[1]\n'
              'runpy.run_path(b.parent / "outside.py")')
    _one_path(_scan(fake_tree, source), "runpy.run_path",
              why="climbs 1 level(s) above the tree root")


def test_f3_differing_path_alternatives(fake_tree):
    source = ('p = Path(__file__) if flag else Path(__file__).parents[1]\n'
              'runpy.run_path(p.parent / "x.py")')
    findings = _scan(fake_tree, source)
    assert len(findings) == 1, findings
    assert (findings[0].file, findings[0].kind, findings[0].name, findings[0].symbol) == (
        "bin/x.py", "path", "runpy.run_path", "<module>",
    )


# GREEN: the selected element or both alternatives stay in the tree.
@pytest.mark.parametrize("source", [
    'a, b = Path(__file__), Path(__file__).parents[1]\nrunpy.run_path(a.parent / "y.py")',
    'p = Path(__file__) if flag else Path(__file__)\nrunpy.run_path(p)',
], ids=["tuple-first", "equal-branches"])
def test_f3_in_tree_alternatives_green(fake_tree, source):
    assert _scan(fake_tree, source) == []


# F4 RED: an unknown or absolute component cannot inherit a trusted base.
@pytest.mark.parametrize("expression,why", [
    ('Path(__file__).parent / param', "unresolved"),
    ('Path(__file__).parent.joinpath("/opt/outside.py")',
     "absolute literal '/opt/outside.py'"),
    ('Path(__file__).parents[1].with_name("outside") / "x.py"',
     "climbs 1 level(s) above the tree root"),
    ('Path(__file__).parents[1].with_suffix(".d") / "x.py"',
     "climbs 1 level(s) above the tree root"),
    ('Path(__file__).parents[1].with_stem("x") / "x.py"',
     "climbs 1 level(s) above the tree root"),
    ('os.path.join(str(Path(__file__).parent), "/opt/x.py")',
     "absolute literal '/opt/x.py'"),
    ('os.path.join(str(Path(__file__).parent), param)', "unresolved"),
    ('Path(str(Path(__file__).parent), "/opt/x.py")', "absolute literal '/opt/x.py'"),
    ('Path(str(Path(__file__).parent), param)', "unresolved"),
], ids=["unknown-division", "absolute-joinpath", "root-with-name", "root-with-suffix",
        "root-with-stem", "absolute-os-join", "unknown-os-join", "absolute-path",
        "unknown-path"])
def test_f4_path_transformations(fake_tree, expression, why):
    _one_path(_scan(fake_tree, f"def f(param):\n    runpy.run_path({expression})"),
              "runpy.run_path", "f", why)


# GREEN: ordinary relative transformations preserve the in-tree base.
@pytest.mark.parametrize("expression", [
    'Path(__file__).parent / "y.py"',
    'Path(__file__).parent.joinpath("y.py")',
    'os.path.join(str(Path(__file__).parent), "y.py")',
    'Path(str(Path(__file__).parent), "y.py")',
    'str(Path(__file__).parent) + os.sep + "y.py"',
    'Path(__file__).with_name("y.py")',
], ids=["division", "joinpath", "os-join", "path", "string-separator", "with-name"])
def test_f4_in_tree_transformations_green(fake_tree, expression):
    assert _scan(fake_tree, f"runpy.run_path({expression})") == []


# F5 RED external/unknown starts; GREEN default and in-tree starts.
@pytest.mark.parametrize("source,symbol,why", [
    ('runpy.run_path(governance_root(Path("/opt/pkg/execution/x.py")) / "outside.py")',
     "<module>", "absolute literal"),
    ('def f(param):\n    runpy.run_path(governance_root(param) / "x.py")',
     "f", "unresolved"),
    ('runpy.run_path(governance_root() / "x.py")', "<module>", None),
    ('runpy.run_path(governance_root(__file__) / "x.py")', "<module>", None),
], ids=["external-red", "unknown-red", "default-green", "in-tree-green"])
def test_f5_layout_helper_start(fake_tree, source, symbol, why):
    _write(fake_tree, "tests/_layout.py",
           "from pathlib import Path\ndef governance_root(start=None):\n"
           "    return Path(start or __file__).resolve().parents[1]\n")
    findings = guard.scan_source(
        IMPORTS + "from _layout import governance_root\n" + source,
        "tests/test_x.py", fake_tree,
    )
    if why is None:
        assert findings == []
    else:
        assert len(findings) == 1, findings
        finding = findings[0]
        assert (finding.file, finding.kind, finding.name, finding.symbol) == (
            "tests/test_x.py", "path", "runpy.run_path", symbol,
        )
        assert finding.why.startswith(why), finding


# F6 RED: an environment override remains possible even with an in-tree fallback.
@pytest.mark.parametrize("source,call", [
    ('runpy.run_path(os.environ.get("PLUGIN_PATH") or Path(__file__))', "runpy.run_path"),
    ('sys.path.insert(0, os.getenv("PLUGIN_PATH") or str(Path(__file__).parent))',
     "sys.path.insert"),
], ids=["run-path", "sys-path"])
def test_f6_environment_or_fallback(fake_tree, source, call):
    _one_path(_scan(fake_tree, source), call)


# F7 RED mutations; GREEN the original imported constant.
@pytest.mark.parametrize("source,symbol,why", [
    ('c.ROOT = Path.home()\nrunpy.run_path(c.ROOT / "outside.py")',
     "<module>", "Path.home()"),
    ('def f(param):\n    setattr(c, "ROOT", param)\n    runpy.run_path(c.ROOT / "x.py")',
     "f", "unresolved"),
    ('runpy.run_path(c.ROOT / "x.py")', "<module>", None),
], ids=["assignment-red", "setattr-red", "unchanged-green"])
def test_f7_imported_constant_mutation(fake_tree, source, symbol, why):
    _write(fake_tree, "pkg/cfg.py",
           "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n")
    findings = _scan(fake_tree, "import pkg.cfg as c\n" + source)
    if why is None:
        assert findings == []
    else:
        _one_path(findings, "runpy.run_path", symbol, why)


# RED: positional mutations may change the program selected by an argv binding.
@pytest.mark.parametrize("mutation", [
    'cmd[2] = "import unshipped_module"',
    'cmd.insert(1, str(Path.home() / "x.py"))',
    'cmd.pop()',
    'cmd.remove("-c")',
    'cmd.clear()',
], ids=["item", "insert", "pop", "remove", "clear"])
def test_f7_argv_positional_mutation(fake_tree, mutation):
    source = ('cmd = [sys.executable, "-c", "import json"]\n'
              f'{mutation}\nsubprocess.run(cmd)')
    findings = _scan(fake_tree, source)
    assert findings, "a mutated argv must not retain the original trusted program"
    assert all(f.file == "bin/x.py" and f.symbol == "<module>" for f in findings)
    assert any((f.kind == "path" and f.name == "subprocess.run") or f.kind == "import"
               for f in findings), findings


# RED: a tail added before a program is selected remains unresolved.
@pytest.mark.parametrize("mutation", [
    'cmd.append(str(Path.home() / "x.py"))',
    'cmd.extend([str(Path.home() / "x.py")])',
    'cmd += [str(Path.home() / "x.py")]',
], ids=["append", "extend", "iadd"])
def test_f7_argv_unknown_tail(fake_tree, mutation):
    source = f"cmd = [sys.executable]\n{mutation}\nsubprocess.run(cmd)"
    _one_path(_scan(fake_tree, source), "subprocess.run")


# GREEN: a tail after -m's operand contains only arguments to that module.
@pytest.mark.parametrize("mutation", ['cmd.append("-q")', 'cmd += ["-q"]'],
                         ids=["append", "iadd"])
def test_f7_module_argument_tail_green(fake_tree, mutation):
    source = f'cmd = [sys.executable, "-m", "pytest"]\n{mutation}\nsubprocess.run(cmd)'
    assert _scan(fake_tree, source) == []


# RED: the production registry assertion must consume keyword-path findings.
def test_production_keyword_loader(make_tree):
    root = make_tree("git")
    _write(root, "bin/kw.py",
           'import importlib.util\nfrom pathlib import Path\n'
           'importlib.util.spec_from_file_location("x", location=Path.home() / "o.py")\n')
    _git(root, "add", "bin/kw.py")
    _production(root, 1, "bin/kw.py", "unregistered")


# R3-A RED: resolve the whole string, including suffix components and traversal.
@pytest.mark.parametrize("expression", [
    'str(Path(__file__).parent) + "suffix/../../outside.py"',
    'f"{Path(__file__).parent}/../../outside.py"',
    'str(Path(__file__).parents[1]) + "x/y.py"',
], ids=["concat-traversal", "fstring-traversal", "root-suffix"])
def test_r3a_string_path_escape(fake_tree, expression):
    _one_path(_scan(fake_tree, f"runpy.run_path({expression})"), "runpy.run_path",
              why="climbs 1 level(s) above the tree root")


@pytest.mark.parametrize("expression", [
    'f"{Path(__file__).parent}{param}"',
    'str(Path(__file__).parent) + param',
], ids=["fstring", "concat"])
def test_r3a_unknown_string_component(fake_tree, expression):
    _one_path(_scan(fake_tree, f"def f(param):\n    runpy.run_path({expression})"),
              "runpy.run_path", "f")


def test_r3a_fstring_conversion_is_unresolved(fake_tree):
    _one_path(_scan(fake_tree, 'runpy.run_path(f"{Path(__file__).parent!r}/y.py")'),
              "runpy.run_path")


# R3-A GREEN: separators append components without replacing the trusted base.
@pytest.mark.parametrize("expression", [
    'f"{Path(__file__).parent}/y.py"',
    'str(Path(__file__).parent) + "/y.py"',
    'f"{Path(__file__).parent}{os.sep}y.py"',
    'str(Path(__file__).parent) + os.sep + "sub" + os.sep + "y.py"',
], ids=["fstring-slash", "concat-slash", "fstring-separator", "concat-separators"])
def test_r3a_in_tree_strings_green(fake_tree, expression):
    assert _scan(fake_tree, f"runpy.run_path({expression})") == []


# R3-B RED: escaped or changed argv lists cannot keep their original trusted code.
@pytest.mark.parametrize("source", [
    'cmd = [sys.executable, "-c", "import json"]\n'
    'other = cmd\nother[2] = "import unshipped_module"\nsubprocess.run(cmd)',
    'cmd = ["echo", "-c", "import unshipped_module"]\n'
    'cmd[0] = sys.executable\nsubprocess.run(cmd)',
    'cmd = []\ncmd.append(sys.executable)\n'
    'cmd.append(str(Path.home() / "x.py"))\nsubprocess.run(cmd)',
    'cmd = [sys.executable, "-c", "import json"]\nmutate(cmd)\nsubprocess.run(cmd)',
    'cmd = [sys.executable, "-c", "import json"]\n'
    'def g():\n    cmd[2] = "import unshipped_module"\ng()\nsubprocess.run(cmd)',
    'holder = {}\ncmd = [sys.executable, "-c", "import json"]\n'
    'holder["c"] = cmd\nsubprocess.run(cmd)',
], ids=["alias", "replace-executable", "append-executable", "call-escape",
        "nested-function", "container-escape"])
def test_r3b_untrusted_python_argv(fake_tree, source):
    findings = _scan(fake_tree, source)
    assert any(f.file == "bin/x.py" and (
        (f.kind, f.name) == ("path", "subprocess.run") or f.kind == "import"
    ) for f in findings), findings


@pytest.mark.parametrize("source", [
    'cmd = [sys.executable, "-c", "import json"]\n'
    'other = cmd\nother[2] = "import unshipped_module"\nsubprocess.run(cmd)',
    'cmd = ["echo", "-c", "import unshipped_module"]\n'
    'cmd[0] = sys.executable\nsubprocess.run(cmd)',
], ids=["alias", "replace-executable"])
def test_r3b_production_untrusted_python_argv(make_tree, source):
    root = make_tree("git")
    rel = "bin/argv.py"
    _write(root, rel, IMPORTS + source + "\n")
    _git(root, "add", rel)
    _production(root, 1, rel, "unregistered")


# R3-B GREEN: read-only uses and repeated runs preserve the documented boundary.
@pytest.mark.parametrize("source", [
    'cmd = [sys.executable, "-m", "pytest"]\n'
    'print(" ".join(cmd))\nsubprocess.run(cmd)',
    'cmd = [sys.executable, "-m", "pytest"]\n'
    'if len(cmd) > 1:\n    subprocess.run(cmd)',
    'cmd = ["git", "status"]\nlog(cmd)\nsubprocess.run(cmd)',
    'cmd = [sys.executable, "-m", "pytest"]\n'
    'subprocess.run(cmd)\nsubprocess.run(cmd, check=True)',
], ids=["print-join", "len", "non-python-escape", "repeated-run"])
def test_r3b_argv_safe_uses_green(fake_tree, source):
    assert _scan(fake_tree, source) == []


# R3-C RED: no known binding may hide an unresolved or outside alternative.
@pytest.mark.parametrize("source", [
    'def f(param):\n    p = Path(__file__)\n    p = param\n    runpy.run_path(p)',
    'def f(param, flag):\n    p = Path(__file__)\n'
    '    if flag:\n        p = param\n    runpy.run_path(p)',
], ids=["reassignment", "branch"])
def test_r3c_unknown_rebinding(fake_tree, source):
    _one_path(_scan(fake_tree, source), "runpy.run_path", "f")


def test_r3c_self_update_escapes(fake_tree):
    source = ('p = Path(__file__).parents[1]\np = p.parent\n'
              'runpy.run_path(p / "outside.py")')
    findings = _scan(fake_tree, source)
    assert findings, "the self-update must resolve against the other binding"
    _one_path(findings, "runpy.run_path", why=findings[0].why)
    assert findings[0].why.startswith("climbs"), findings


# R3-C GREEN: self-updates and differing branches may all remain inside the tree.
@pytest.mark.parametrize("source", [
    'd = Path(__file__).parent / "a"\nd = d / "b"\nrunpy.run_path(d)',
    'd = Path(__file__).parent\nfor sub in ("a", "b"):\n'
    '    d = d / sub\nrunpy.run_path(d)',
    'R = Path(__file__).parent\nif os.name == "nt":\n'
    '    R = Path(__file__).parent / "win"\nrunpy.run_path(R / "y.py")',
], ids=["self-update", "loop-update", "branch-paths"])
def test_r3c_in_tree_bindings_green(fake_tree, source):
    assert _scan(fake_tree, source) == []


# H1 RED: assignments add import paths just as insert and append do.
@pytest.mark.parametrize("source,symbol,why", [
    ('sys.path[:0] = [str(Path.home())]', "<module>", "Path.home()"),
    ('sys.path = [str(Path.home())] + sys.path', "<module>", "Path.home()"),
    ('sys.path += [os.environ["X"]]', "<module>", "unresolved"),
    ('def f(param):\n    sys.path[0] = param', "f", "unresolved"),
], ids=["slice", "prepend", "iadd", "item"])
def test_h1_sys_path_assignment(fake_tree, source, symbol, why):
    _one_path(_scan(fake_tree, source), "sys.path", symbol, why)


# H1 GREEN: retained entries add nothing; new in-tree entries are allowed.
@pytest.mark.parametrize("source", [
    'saved = list(sys.path)\nsys.path[:] = saved',
    'sys.path[:] = [p for p in sys.path if p]',
    'sys.path = [str(Path(__file__).parent)] + sys.path',
    'sys.path[:] = sys.path.copy()',
], ids=["saved", "filtered", "in-tree-prepend", "copy"])
def test_h1_sys_path_assignment_green(fake_tree, source):
    assert _scan(fake_tree, source) == []


# H2 RED: extend grades each added entry, including unknown iterables.
@pytest.mark.parametrize("source,symbol,why", [
    ('sys.path.extend([str(Path.home())])', "<module>", "Path.home()"),
    ('def f(param):\n    sys.path.extend(param)', "f", "unresolved"),
], ids=["home", "unknown"])
def test_h2_sys_path_extend(fake_tree, source, symbol, why):
    _one_path(_scan(fake_tree, source), "sys.path.extend", symbol, why)


def test_h2_sys_path_extend_green(fake_tree):
    assert _scan(fake_tree, 'sys.path.extend([str(Path(__file__).parent)])') == []


# H3 RED: addsitedir accepts both positional and keyword paths.
@pytest.mark.parametrize("source,symbol,why", [
    ('site.addsitedir(str(Path.home()))', "<module>", "Path.home()"),
    ('def f(param):\n    site.addsitedir(sitedir=param)', "f", "unresolved"),
], ids=["home", "keyword"])
def test_h3_site_addsitedir(fake_tree, source, symbol, why):
    _one_path(_scan(fake_tree, "import site\n" + source), "site.addsitedir", symbol, why)


# H4 RED: package search paths are import paths too.
@pytest.mark.parametrize("source,call,symbol,why", [
    ('import pkg\npkg.__path__.append(str(Path.home()))',
     "pkg.__path__.append", "<module>", "Path.home()"),
    ('import pkg\npkg.__path__ = [str(Path.home())]',
     "pkg.__path__", "<module>", "Path.home()"),
    ('def f(param):\n    __path__.insert(0, param)',
     "__path__.insert", "f", "unresolved"),
], ids=["append", "assignment", "module-path"])
def test_h4_package_path(fake_tree, source, call, symbol, why):
    _one_path(_scan(fake_tree, source), call, symbol, why)


# H5 RED: file loaders and spec package locations must remain inside the tree.
@pytest.mark.parametrize("source,call,symbol,why", [
    ('importlib.machinery.SourcelessFileLoader("x", str(Path.home() / "x.pyc"))',
     "importlib.machinery.SourcelessFileLoader", "<module>", "Path.home()"),
    ('def f(param):\n    importlib.machinery.ExtensionFileLoader("x", path=param)',
     "importlib.machinery.ExtensionFileLoader", "f", "unresolved"),
    ('import zipimport\nzipimport.zipimporter(str(Path.home() / "a.zip"))',
     "zipimport.zipimporter", "<module>", "Path.home()"),
    ('importlib.util.spec_from_file_location("x", str(Path(__file__).with_name("y.py")), '
     'submodule_search_locations=[str(Path.home())])',
     "importlib.util.spec_from_file_location", "<module>", "Path.home()"),
], ids=["sourceless", "extension-keyword", "zip", "spec-package-path"])
def test_h5_file_loaders(fake_tree, source, call, symbol, why):
    _one_path(_scan(fake_tree, source), call, symbol, why)


# H6 RED: literal module names resolve as imports; unknown names fail closed.
@pytest.mark.parametrize("source", [
    'runpy.run_module("unshipped_module")',
    'import builtins\nbuiltins.__import__("unshipped_module")',
], ids=["run-module", "builtins-import"])
def test_h6_dynamic_module_import(fake_tree, source):
    findings = _scan(fake_tree, source)
    assert [(f.file, f.kind, f.name, f.symbol) for f in findings] == [
        ("bin/x.py", "import", "unshipped_module", "<module>"),
    ]


@pytest.mark.parametrize("call", ["runpy.run_module", "importlib.__import__"],
                         ids=["run-module", "importlib-import"])
def test_h6_dynamic_module_unknown(fake_tree, call):
    _one_path(_scan(fake_tree, f"def f(param):\n    {call}(param)"), call, "f")


def test_h6_stdlib_run_module_green(fake_tree):
    assert _scan(fake_tree, 'runpy.run_module("json")') == []


# H7 RED: a global write changes the binding read by another function.
def test_h7_global_path_rebound(fake_tree):
    source = ('ROOT = Path(__file__).parent\n'
              'def setup():\n    global ROOT\n    ROOT = Path.home()\n'
              'def load():\n    runpy.run_path(ROOT / "x.py")')
    _one_path(_scan(fake_tree, source), "runpy.run_path", "load", "Path.home()")


def test_h7_global_in_tree_rebound_green(fake_tree):
    source = ('ROOT = Path(__file__).parent\n'
              'def setup():\n    global ROOT\n'
              '    ROOT = Path(__file__).parent / "sub"\n'
              'def load():\n    runpy.run_path(ROOT / "x.py")')
    assert _scan(fake_tree, source) == []


# H8 RED: shipped symlinks must not escape even when their targets are clean.
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
@pytest.mark.parametrize("layout,rel,directory", [
    ("git", "bin/linked.py", False),
    ("git", "lib", True),
    ("export-tree", "bin/linked.py", False),
], ids=["git-file", "git-directory", "export-file"])
def test_h8_production_escaping_symlink(make_tree, tmp_path, layout, rel, directory):
    root = make_tree(layout)
    _write(tmp_path, "outside/clean.py")
    target = tmp_path / "outside" if directory else tmp_path / "outside/clean.py"
    assert not target.is_relative_to(root)
    try:
        os.symlink(target, root / rel, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    if layout == "git":
        _git(root, "add", rel)
    else:
        _marker(root)
    _production(root, 1, rel)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_h8_shipped_files_reports_escaping_symlink(make_tree, tmp_path):
    root = make_tree("git")
    _write(tmp_path, "outside.py")
    rel = "bin/linked.py"
    try:
        os.symlink(tmp_path / "outside.py", root / rel)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _git(root, "add", rel)
    problems = guard.shipped_files(root).problems
    assert any(isinstance(problem, str) and rel in problem for problem in problems), problems


# H8 GREEN: a relative symlink to a shipped file inside the tree is allowed.
@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_h8_production_in_tree_symlink_green(make_tree):
    root = make_tree("git")
    try:
        os.symlink("inside.py", root / "bin/alias.py")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _git(root, "add", "bin/alias.py")
    _production(root, 0)
