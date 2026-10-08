"""No shipped module may reach outside the shipped tree without saying so.

The 2.1.3 scrubber incident: a shipped module put a directory that only exists in the
monorepo (or under the developer's ``~/.claude``) on ``sys.path``, imported from it,
caught the ImportError, and degraded silently. Every public install lost the feature;
doctor stayed green. `test_export_allowlist.py` and `test_export_withheld_callers.py`
close the ``agentica_core`` import graph only, so nothing caught the pattern.

This guard AST-walks every shipped ``.py`` and flags two things:

  * a static import (function-local ones included) that resolves to none of: the
    standard library, a dependency declared in ``pyproject.toml``, the test runner, or
    a module inside the shipped tree;
  * a ``sys.path.insert/append``, ``spec_from_file_location``, ``run_path`` or
    ``SourceFileLoader`` whose path argument is derived from outside the tree: a
    ``.parent``/``parents[n]`` chain that climbs above the tree root, ``Path.home()``,
    ``expanduser``, or a ``HOME``/``USERPROFILE`` environment read. Provenance is
    followed through simple name assignments, so ``d = Path.home() / "x";
    sys.path.insert(0, str(d))`` is caught, not just the literal call.

Each finding must match an entry in ``config/out_of_package_imports.json`` that says
what the site reaches for, why, how it degrades and where that degrade is visible
(a doctor row or a payload field). The test fails on an unregistered site, a stale
entry, and any entry that declares its degrade ``silent``.

One registry serves two trees: the curated export the monorepo builds and the public
product repo (``tree_kind``). An entry's ``scope`` says where its site exists:
``shared`` (the default), ``export-only`` or ``public-only``. A site must be present
where its scope applies and absent where it does not; a site that turns up in the
other tree has to be promoted to ``shared``. An optional ``visibility_test`` names the
pytest node that proves the degrade, and must exist in the tree where the site applies.

`test_no_new_above_pack_hops.py` ratchets how a hop is SPELLED in pack modules; this
guard checks what an import actually REACHES, across agentica_core and tests too.

The file ships with the export, so the CI ``export-gate`` job runs it against the
built tree. In the monorepo it builds the export into a temp dir first: the monorepo
layout resolves imports the public tree cannot, so scanning it would prove nothing.

Run as a script, it is also the runtime half of the guard (the export-gate step that
runs doctor with an empty ``HOME``)::

    python tests/test_import_boundary.py --check-doctor doctor_output.txt
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

TREE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "config/out_of_package_imports.json"
FIXTURE_REL = "tests/falsifiability_fixtures/import_boundary"

# Directories whose .py files are data for a test, not shipped code paths. Dot
# directories (.tmp, .venv, .pytest_cache) are runtime scratch: the suite itself writes
# fake `hook_registry.py` trees under .tmp/, which would otherwise "resolve" imports.
_SKIP_PARTS = {"falsifiability_fixtures", "__pycache__"}


def _skipped(rel: Path) -> bool:
    return any(part in _SKIP_PARTS or part.startswith(".") for part in rel.parts)

# Test-only third-party modules the exported suite needs (requirements-dev.txt).
_DEV_MODULES = {"pytest", "_pytest"}

# Canonical dotted names (import aliases are resolved first, so `import sys as _sys;
# _sys.path.insert(...)` and `from runpy import run_path` both land here).
_PATH_CALLS = {
    "sys.path.insert": 1,
    "sys.path.append": 0,
    "importlib.util.spec_from_file_location": 1,
    "runpy.run_path": 0,
    "importlib.machinery.SourceFileLoader": 1,
}
_DYNAMIC_IMPORTS = {"importlib.import_module", "__import__"}
_HOME_ENV = {"HOME", "USERPROFILE"}


# --------------------------------------------------------------------------- scanning


@dataclass(frozen=True)
class Finding:
    file: str      # tree-relative, posix
    symbol: str    # enclosing def/class qualname, or "<module>"
    kind: str      # "import" | "path"
    name: str      # dotted module name, or the path call's dotted name
    line: int
    why: str = ""

    def key(self) -> tuple[str, str]:
        return self.file, self.symbol


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _declared_modules(tree_root: Path) -> set[str]:
    """Import names of the runtime dependencies pyproject.toml declares."""
    pyproject = tree_root / "pyproject.toml"
    if not pyproject.is_file():
        return set()
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get(
        "dependencies", [])
    out = set()
    for spec in deps:
        name = re.split(r"[\s<>=!~;\[]", spec.strip(), maxsplit=1)[0]
        out.add(name.lower().replace("-", "_"))
    return out


def shipped_python_files(tree_root: Path) -> list[Path]:
    return sorted(
        p for p in tree_root.rglob("*.py") if not _skipped(p.relative_to(tree_root))
    )


def _scope_nodes(body: list[ast.stmt]):
    """Every node of one lexical scope, without descending into nested defs/classes/lambdas.

    A function-local `D = Path.home()` must not taint a module-level `D`, and vice versa
    (a nested scope's own assignments are collected when the scanner enters it)."""
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    stack: list[ast.AST] = [n for n in body if not isinstance(n, nested)]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, nested))


def _bindings(body: list[ast.stmt]) -> dict[str, list[ast.AST]]:
    frame: dict[str, list[ast.AST]] = {}
    for node in _scope_nodes(body):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets, value = [node.target], node.iter
        if value is None:
            continue
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    frame.setdefault(n.id, []).append(value)
    return frame


# A resolved path expression: ("in", parts) = a directory/file inside the tree, given
# as parts below the root; ("out", why) = outside the tree; None = not derivable.
_Resolved = tuple[str, object] | None


class _Scanner(ast.NodeVisitor):
    """Collects one file's findings, tracking the enclosing scope and its own bindings."""

    def __init__(self, rel: str, resolver: "_ImportResolver | None",
                 locator: "_ImportResolver | None" = None, depth: int = 0):
        self.rel = rel
        self.resolver = resolver
        # Finds the module that defines an imported constant (pass 1 has no resolver yet).
        self.locator = locator or resolver
        self.depth = depth
        self.file_parts = tuple(Path(rel).parts)
        self.scope: list[str] = []
        self.frames: list[dict[str, list[ast.AST]]] = []
        self.findings: list[Finding] = []
        self.inserted: set[tuple[str, ...]] = set()  # in-tree dirs put on sys.path
        self.aliases: dict[str, str] = {}             # local name -> canonical dotted name

    def _canonical(self, node: ast.AST) -> str:
        dotted = _dotted(node)
        head, _, rest = dotted.partition(".")
        target = self.aliases.get(head)
        if target is None:
            return dotted
        return f"{target}.{rest}" if rest else target

    # scope bookkeeping -------------------------------------------------------------

    def _symbol(self) -> str:
        return ".".join(self.scope) or "<module>"

    def visit_Module(self, node):  # noqa: N802
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for a in sub.names:
                    if a.asname:
                        self.aliases[a.asname] = a.name
            elif isinstance(sub, ast.ImportFrom) and (sub.module or sub.level):
                pkg = ".".join(self.file_parts[:-1][:len(self.file_parts) - sub.level]) \
                    if sub.level else ""
                base = ".".join(x for x in (pkg, sub.module or "") if x)
                for a in sub.names:
                    self.aliases[a.asname or a.name] = f"{base}.{a.name}"
        self.frames.append(_bindings(node.body))
        self.generic_visit(node)
        self.frames.pop()

    def _enter(self, node, name: str) -> None:
        self.scope.append(name)
        self.frames.append(_bindings(node.body))
        self.generic_visit(node)
        self.frames.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node):  # noqa: N802
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._enter(node, node.name)

    def visit_ClassDef(self, node):  # noqa: N802
        self._enter(node, node.name)

    def _lookup(self, name: str) -> list[ast.AST]:
        for frame in reversed(self.frames):
            if name in frame:
                return frame[name]
        return []

    # imports -----------------------------------------------------------------------

    def visit_Import(self, node):  # noqa: N802
        if self.resolver is None:
            return
        for alias in node.names:
            if not self.resolver.resolves(alias.name, self.file_parts):
                self.findings.append(
                    Finding(self.rel, self._symbol(), "import", alias.name, node.lineno))

    def visit_ImportFrom(self, node):  # noqa: N802
        if self.resolver is None:
            return
        names = [a.name for a in node.names]
        if node.level:
            missing = self.resolver.missing_relative(node.module, node.level, names,
                                                     self.file_parts)
            prefix = "." * node.level + (node.module or "")
        else:
            missing = self.resolver.missing_from(node.module or "", names, self.file_parts)
            prefix = node.module or ""
        for name in missing:
            dotted = prefix if name is None else (f"{prefix}.{name}" if not prefix.endswith(".")
                                                   else f"{prefix}{name}")
            self.findings.append(
                Finding(self.rel, self._symbol(), "import", dotted, node.lineno))

    # path provenance ---------------------------------------------------------------

    def visit_Call(self, node):  # noqa: N802
        name = self._canonical(node.func)
        if name in _DYNAMIC_IMPORTS and self.resolver is not None and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            # A literal dynamic import is still an import: `import_module("x.y")`.
            target = node.args[0].value
            if target.startswith("."):
                level = len(target) - len(target.lstrip("."))
                ok = not self.resolver.missing_relative(target.lstrip(".") or None, level,
                                                        [], self.file_parts)
            else:
                ok = self.resolver.resolves(target, self.file_parts)
            if not ok:
                self.findings.append(
                    Finding(self.rel, self._symbol(), "import", target, node.lineno))
        idx = _PATH_CALLS.get(name)
        if idx is not None and len(node.args) > idx:
            res = self._resolve(node.args[idx], 0, frozenset())
            if res is not None and res[0] == "out":
                self.findings.append(Finding(self.rel, self._symbol(), "path", name,
                                             node.lineno, str(res[1])))
            elif res is not None and name.startswith("sys.path"):
                self.inserted.add(tuple(res[1]))  # type: ignore[arg-type]
        self.generic_visit(node)

    def _resolve(self, node: ast.AST, depth: int, seen: frozenset) -> _Resolved:
        """Where the path expression `node` points, following simple name bindings."""
        if depth > 16:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("~"):
                return ("out", f"literal {v!r}")
            if v.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", v):
                return ("out", f"absolute literal {v!r}")
            return None
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return ("in", self.file_parts)
            if node.id in seen:
                return None
            found: _Resolved = None
            values = self._lookup(node.id)
            for value in values:
                r = self._resolve(value, depth + 1, seen | {node.id})
                if r is not None and r[0] == "out":
                    return r
                found = found or r
            if not values and node.id in self.aliases:
                return self._imported_constant(self.aliases[node.id])
            return found
        if isinstance(node, ast.JoinedStr):
            return self._first_out(node.values, depth, seen)
        if isinstance(node, ast.FormattedValue):
            return self._resolve(node.value, depth + 1, seen)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return self._first_out(node.elts, depth, seen)
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                return _up(self._resolve(node.value, depth + 1, seen), 1)
            if node.attr in {"resolve", "absolute"}:
                return self._resolve(node.value, depth + 1, seen)
            return None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "parents":
            base = self._resolve(node.value.value, depth + 1, seen)
            n = node.slice
            if isinstance(n, ast.Constant) and isinstance(n.value, int):
                return _up(base, n.value + 1)
            return base if base is not None and base[0] == "out" else None
        if isinstance(node, ast.Call):
            return self._resolve_call(node, depth, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
            base = self._resolve(node.left, depth + 1, seen)
            if base is not None and base[0] == "out":
                return base
            right = self._resolve(node.right, depth + 1, seen)
            if right is not None and right[0] == "out" and isinstance(node.op, ast.Div):
                return right  # `x / "/abs"` replaces the path entirely
            if base is not None and isinstance(node.right, ast.Constant) \
                    and isinstance(node.right.value, str):
                return _down(base, node.right.value)
            return base
        return None

    def _imported_constant(self, dotted: str) -> _Resolved:
        """Resolve `from pkg.mod import NAME` where NAME is a path constant: evaluate NAME
        inside the module that defines it (following re-imports a few hops)."""
        module, _, name = dotted.rpartition(".")
        if self.locator is None or not module or self.depth > 4:
            return None
        found = self.locator._find(module, self.file_parts)
        if found is None or found.suffix != ".py":
            return None
        rel = found.relative_to(self.locator.tree_root).as_posix()
        try:
            tree = ast.parse(found.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return None
        other = _Scanner(rel, None, self.locator, self.depth + 1)
        other.visit_Module_bindings(tree)
        return other._resolve(ast.Name(id=name, ctx=ast.Load()), 0, frozenset())

    def visit_Module_bindings(self, tree: ast.Module) -> None:  # noqa: N802
        """Load a module's import aliases and top-level bindings without scanning it."""
        for sub in ast.walk(tree):
            if isinstance(sub, ast.Import):
                for a in sub.names:
                    if a.asname:
                        self.aliases[a.asname] = a.name
            elif isinstance(sub, ast.ImportFrom) and (sub.module or sub.level):
                pkg = ".".join(self.file_parts[:-1][:len(self.file_parts) - sub.level]) \
                    if sub.level else ""
                base = ".".join(x for x in (pkg, sub.module or "") if x)
                for a in sub.names:
                    self.aliases[a.asname or a.name] = f"{base}.{a.name}"
        self.frames = [_bindings(tree.body)]

    def _first_out(self, nodes, depth, seen) -> _Resolved:
        found: _Resolved = None
        for n in nodes:
            r = self._resolve(n, depth + 1, seen)
            if r is not None and r[0] == "out":
                return r
            found = found or r
        return found

    def _resolve_call(self, node: ast.Call, depth: int, seen: frozenset) -> _Resolved:
        fn = node.func
        d = self._canonical(fn)
        if d.endswith("Path.home") or d == "home":
            return ("out", "Path.home()")
        if d.endswith("expanduser"):
            return ("out", "expanduser")
        if d in {"os.environ.get", "os.getenv"} and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant) and a.value in _HOME_ENV:
                return ("out", f"env {a.value}")
            # An operator override with an in-code default: grade the default.
            return self._resolve(node.args[1], depth + 1, seen) if len(node.args) > 1 else None
        if isinstance(fn, ast.Attribute) and fn.attr in {"resolve", "absolute"}:
            return self._resolve(fn.value, depth + 1, seen)
        if isinstance(fn, ast.Attribute) and fn.attr == "joinpath":
            base = self._resolve(fn.value, depth + 1, seen)
            for a in node.args:
                if base is None or base[0] == "out":
                    break
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    base = _down(base, a.value)
            return base
        if d in {"pathlib.Path", "Path", "str", "os.fspath", "os.path.abspath",
                 "os.path.realpath", "os.path.normpath", "os.path.expandvars"} and node.args:
            return self._resolve(node.args[0], depth + 1, seen)
        if d == "os.path.dirname" and node.args:
            return _up(self._resolve(node.args[0], depth + 1, seen), 1)
        if d == "os.path.join" and node.args:
            base = self._resolve(node.args[0], depth + 1, seen)
            for a in node.args[1:]:
                if base is None or base[0] == "out":
                    break
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    base = _down(base, a.value)
            return base
        return None


def _up(res: _Resolved, n: int) -> _Resolved:
    if res is None or res[0] == "out":
        return res
    parts = tuple(res[1])  # type: ignore[arg-type]
    if n > len(parts):
        # Escaping is sticky: `ROOT.parent / "docs"` climbs out and back down into a
        # sibling, which is still outside the tree.
        return ("out", f"climbs {n - len(parts)} level(s) above the tree root")
    return ("in", parts[:len(parts) - n])


def _down(res: _Resolved, part: str) -> _Resolved:
    if res is None or res[0] == "out":
        return res
    parts = list(res[1])  # type: ignore[arg-type]
    for c in Path(part).parts:
        if c == "..":
            if not parts:
                return ("out", "climbs 1 level(s) above the tree root")
            parts.pop()
        elif c not in {".", ""}:
            parts.append(c)
    return ("in", tuple(parts))


class _ImportResolver:
    """Does a dotted module (and each name imported from it) resolve in a PUBLIC install?

    Search roots for a file: the tree root (pytest's rootdir, an installed package, doctor's
    own insert), the file's own directory (a script's sys.path[0]) and every in-tree
    directory some shipped file puts on sys.path. Third-party names must be stdlib, a
    dependency declared in pyproject.toml, or the test runner. A dotted name resolves only
    when every segment exists, and `from pkg import name` needs `name` to be a shipped
    submodule or a name the module actually defines, so a package that exists cannot mask
    a withheld submodule (`from agentica_core import automation_health`)."""

    def __init__(self, tree_root: Path, inserted: set[tuple[str, ...]]):
        self.tree_root = tree_root
        self.third_party = _declared_modules(tree_root) | _DEV_MODULES
        self.roots = [()] + sorted(inserted)
        self._defined: dict[Path, set[str] | None] = {}

    def _locate(self, base: tuple[str, ...], dotted: str) -> Path | None:
        p = self.tree_root.joinpath(*base, *dotted.split("."))
        if p.with_suffix(".py").is_file():
            return p.with_suffix(".py")
        if p.is_dir() and not _skipped(p.relative_to(self.tree_root)):
            return p
        return None

    def _find(self, dotted: str, file_parts: tuple[str, ...]) -> Path | None:
        for base in (*self.roots, file_parts[:-1]):
            found = self._locate(base, dotted)
            if found is not None:
                return found
        return None

    def _external(self, dotted: str) -> bool:
        top = dotted.split(".", 1)[0]
        return top in sys.stdlib_module_names or top in self.third_party

    def resolves(self, dotted: str, file_parts: tuple[str, ...]) -> bool:
        return self._external(dotted) or self._find(dotted, file_parts) is not None

    def _defined_names(self, module_file: Path) -> set[str] | None:
        """Top-level names a module binds; None when it can bind anything (star import,
        module __getattr__) or cannot be parsed."""
        if module_file not in self._defined:
            names: set[str] | None = set()
            try:
                tree = ast.parse(module_file.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                tree = None
            if tree is None:
                names = None
            else:
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        names.add(node.name)
                        if node.name == "__getattr__":
                            names = None
                            break
                for node in (_scope_nodes(tree.body) if names is not None else ()):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        if any(a.name == "*" for a in node.names):
                            names = None
                            break
                        for a in node.names:
                            names.add(a.asname or a.name.split(".")[0])
                    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                        names.add(node.id)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                           ast.ClassDef)):
                        names.add(node.name)
            self._defined[module_file] = names
        return self._defined[module_file]

    def _missing_names(self, module: Path, names: list[str]) -> list[str]:
        if module.is_dir():
            init = module / "__init__.py"
            defined = self._defined_names(init) if init.is_file() else set()
            return [n for n in names
                    if self._locate((), str((module / n).relative_to(self.tree_root))
                                    .replace("/", ".")) is None
                    and defined is not None and n not in defined]
        defined = self._defined_names(module)
        return [] if defined is None else [n for n in names if n not in defined]

    def missing_from(self, module: str, names: list[str],
                     file_parts: tuple[str, ...]) -> list[str | None]:
        """Unresolvable parts of `from module import names` ([None] = the module itself)."""
        if self._external(module):
            return []
        found = self._find(module, file_parts)
        if found is None:
            return [None]
        return self._missing_names(found, [n for n in names if n != "*"])

    def missing_relative(self, module: str | None, level: int, names: list[str],
                         file_parts: tuple[str, ...]) -> list[str | None]:
        pkg = file_parts[:-1]
        if level - 1 > len(pkg):
            return [None]
        base = pkg[:len(pkg) - (level - 1)]
        target = self.tree_root.joinpath(*base)
        if module:
            found = self._locate(base, module)
            if found is None:
                return [None]
            target = found
        return self._missing_names(target, [n for n in names if n != "*"])


def scan_tree(tree_root: Path) -> list[Finding]:
    sources = {p.relative_to(tree_root).as_posix(): p.read_text(encoding="utf-8",
                                                                  errors="replace")
               for p in shipped_python_files(tree_root)}
    # Pass 1: which in-tree directories does shipped code put on sys.path?
    inserted: set[tuple[str, ...]] = set()
    locator = _ImportResolver(tree_root, set())
    for rel, src in sources.items():
        scanner = _scan(src, rel, None, locator)
        if scanner:
            inserted |= scanner.inserted
    resolver = _ImportResolver(tree_root, inserted)
    findings: list[Finding] = []
    for rel, src in sources.items():
        scanner = _scan(src, rel, resolver)
        if scanner:
            findings.extend(scanner.findings)
    return findings


def _scan(source: str, rel: str, resolver: "_ImportResolver | None",
          locator: "_ImportResolver | None" = None) -> "_Scanner | None":
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return None
    scanner = _Scanner(rel, resolver, locator)
    scanner.visit(tree)
    return scanner


def scan_source(source: str, rel: str, tree_root: Path) -> list[Finding]:
    """Findings for one source file placed at `rel` inside `tree_root`."""
    scanner = _scan(source, rel, _ImportResolver(tree_root, set()))
    return scanner.findings if scanner else []


# --------------------------------------------------------------------------- registry


_REQUIRED_FIELDS = ("file", "symbol", "imports", "path_calls", "target", "reason",
                    "degrade", "visibility", "surface")
_SCOPES = ("shared", "export-only", "public-only")
# "silent" is deliberately absent: a silent degrade is fixed, never registered.
_VISIBILITY = {"visible", "fallback", "optional", "dead_code", "no_degrade"}


def tree_kind(tree_root: Path) -> str:
    """"public" for the product repo, "export" for the curated tree built from the monorepo.

    licensing.py is public-owned (extract_public.PUBLIC_OWNED_MODULES): the export never
    contains it, so its presence is what tells the two trees apart."""
    return "public" if (tree_root / "agentica_core" / "licensing.py").is_file() else "export"


def _applies(site: dict, kind: str) -> bool:
    """Whether the entry's site is expected to exist in a `kind` tree."""
    scope = site.get("scope", "shared")
    return scope == "shared" or scope == f"{kind}-only"


def load_registry(tree_root: Path) -> list[dict]:
    """Registry sites with any shared `class` fields filled in."""
    data = json.loads((tree_root / REGISTRY_REL).read_text(encoding="utf-8"))
    classes = data.get("classes", {})
    sites = []
    for site in data["sites"]:
        cls = classes.get(site.get("class"), {}) if "class" in site else {}
        if "class" in site and not cls:
            cls = {"visibility": f"unknown class {site['class']!r}"}
        sites.append({**cls, **site})
    return sites


def registry_problems(findings: list[Finding], sites: list[dict],
                      tree_kind: str = "export") -> list[str]:
    """Every way the findings and the registry disagree in a `tree_kind` tree
    ("export" or "public"). Empty means clean."""
    problems: list[str] = []
    grouped: dict[tuple[str, str], dict[str, set[str]]] = {}
    for f in findings:
        g = grouped.setdefault(f.key(), {"import": set(), "path": set()})
        g[f.kind].add(f.name)
    seen_keys: set[tuple[str, str]] = set()
    promote: set[tuple[str, str]] = set()
    applicable: list[dict] = []
    for site in sites:
        missing = [k for k in _REQUIRED_FIELDS if k not in site]
        key = (site.get("file", "?"), site.get("symbol", "?"))
        if missing:
            problems.append(f"{key}: registry entry missing fields {missing}")
            continue
        if key in seen_keys:
            problems.append(f"{key}: duplicate registry entry")
        seen_keys.add(key)
        scope = site.get("scope", "shared")
        if scope not in _SCOPES:
            problems.append(f"{key}: unknown scope {scope!r} (expected one of {list(_SCOPES)})")
            continue
        if not _applies(site, tree_kind):
            if key in grouped:
                problems.append(f"{key}: registered {scope} but present in this {tree_kind} "
                                f"tree -- promote the entry to scope 'shared'")
                promote.add(key)
            continue
        applicable.append(site)
    for site in applicable:
        key = (site["file"], site["symbol"])
        if site["visibility"] not in _VISIBILITY or not str(site["surface"]).strip():
            problems.append(f"{key}: degrade is {site['visibility']!r} with surface "
                            f"{site['surface']!r} -- a silent degrade must be fixed, "
                            f"not registered")
        actual = grouped.get(key)
        if actual is None:
            problems.append(f"{key}: stale registry entry -- no such out-of-package site")
            continue
        for kind, field in (("import", "imports"), ("path", "path_calls")):
            declared = set(site[field])
            if declared - actual[kind]:
                problems.append(f"{key}: stale {field} {sorted(declared - actual[kind])}")
    for key, actual in sorted(grouped.items()):
        if key in promote:
            continue  # already reported once, as a promotion
        site = next((s for s in sites if _applies(s, tree_kind)
                     and (s.get("file"), s.get("symbol")) == key), None)
        declared_i = set(site.get("imports", [])) if site else set()
        declared_p = set(site.get("path_calls", [])) if site else set()
        extra_i = actual["import"] - declared_i
        extra_p = actual["path"] - declared_p
        if extra_i or extra_p:
            lines = sorted({f.line for f in findings if f.key() == key})
            problems.append(f"{key} (lines {lines}): unregistered out-of-package "
                            f"imports {sorted(extra_i)} path calls {sorted(extra_p)}")
    return problems


def visibility_test_problems(sites: list[dict], tree_root: Path,
                             tree_kind: str = "export") -> list[str]:
    """Registry `visibility_test` node ids ("<path>::<test name>") that do not exist in
    `tree_root`. Only entries whose site applies to this kind of tree are checked: the
    test for an export-only site need not exist in the public repo."""
    problems: list[str] = []
    for site in sites:
        node = site.get("visibility_test")
        if not node or not _applies(site, tree_kind):
            continue
        key = (site.get("file", "?"), site.get("symbol", "?"))
        rel, _, name = str(node).partition("::")
        name = name.split("[", 1)[0]
        path = tree_root / rel
        if not rel or not name or not path.is_file():
            problems.append(f"{key}: visibility_test {node!r} -- no such file in this tree")
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            problems.append(f"{key}: visibility_test {node!r} -- {rel} does not parse")
            continue
        defined = {n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if name not in defined:
            problems.append(f"{key}: visibility_test {node!r} -- no top-level test named {name}")
    return problems


# --------------------------------------------------------------------------- doctor half


_ROW_RE = re.compile(r"^\s*\[(OK|WARN|FAIL|ERROR|SKIP)\]\s+([^:\s]+)")


def doctor_problems(doctor_output: str, exit_code: int, sites: list[dict]) -> list[str]:
    """Doctor run with an empty HOME: every registered doctor row must read WARN, and
    doctor itself must report no failures."""
    rows: dict[str, str] = {}
    for line in doctor_output.splitlines():
        m = _ROW_RE.match(line)
        if m:
            rows.setdefault(m.group(2), m.group(1))
    problems: list[str] = []
    for site in sites:
        label = site.get("doctor_row")
        if not label:
            continue
        status = rows.get(label)
        if status is None:
            problems.append(f"doctor printed no {label!r} row ({site['file']}:{site['symbol']})")
        elif status != "WARN":
            problems.append(f"doctor row {label!r} is {status}, expected WARN under an empty "
                            f"HOME ({site['file']}:{site['symbol']})")
    if exit_code != 0:
        problems.append(f"doctor exited {exit_code} under an empty HOME")
    return problems


# --------------------------------------------------------------------------- the tree


def _build_export(dest: Path) -> Path:
    subprocess.run(
        [sys.executable, str(TREE_ROOT / "bin" / "extract_public.py"), "--dest", str(dest)],
        check=True, capture_output=True, text=True, timeout=300,
    )
    return dest


@pytest.fixture(scope="module")
def shipped_tree(tmp_path_factory) -> Path:
    """The tree a public install gets: this one when we ARE the export, else a fresh build."""
    if (TREE_ROOT / "agentica_core").is_dir():
        return TREE_ROOT
    return _build_export(tmp_path_factory.mktemp("import-boundary") / "export")


# --------------------------------------------------------------------------- tests


def test_every_out_of_package_site_is_registered_and_visible(shipped_tree):
    findings = scan_tree(shipped_tree)
    kind = tree_kind(shipped_tree)
    sites = load_registry(shipped_tree)
    problems = (registry_problems(findings, sites, kind)
                + visibility_test_problems(sites, shipped_tree, kind))
    assert not problems, "\n".join(problems)


def test_scan_covers_the_shipped_packages(shipped_tree):
    """A scanner that walks nothing passes vacuously."""
    rels = {p.relative_to(shipped_tree).parts[0] for p in shipped_python_files(shipped_tree)}
    assert {"agentica_core", "bin", "execution", "tests"} <= rels


@pytest.fixture
def fake_tree(tmp_path) -> Path:
    """A minimal shipped layout: bin/, execution/ (with one module) and a package."""
    files = {"bin/inside_module.py": "", "execution/__init__.py": "",
             "execution/doctor.py": "def main():\n    pass\n",
             "pkg/__init__.py": "from .sibling import a\nVERSION = 1\n",
             "pkg/sibling.py": "a = 1\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def _kinds(findings) -> set[tuple[str, str]]:
    return {(f.kind, f.name) for f in findings}


def test_bad_fixture_is_flagged_and_clean_fixture_is_not(fake_tree):
    """Falsifiability: the 2.1.3 shape is caught; an in-tree sys.path insert is not."""
    fixtures = TREE_ROOT / FIXTURE_REL
    bad = scan_source((fixtures / "bad" / "reach_out.py").read_text(encoding="utf-8"),
                      "bin/reach_out.py", fake_tree)
    assert _kinds(bad) == {("import", "principle_audit"), ("path", "sys.path.insert")}
    assert registry_problems(bad, []), "an unregistered reach-out must be a problem"
    clean = scan_source((fixtures / "clean" / "reach_in.py").read_text(encoding="utf-8"),
                        "bin/reach_in.py", fake_tree)
    assert clean == []


@pytest.mark.parametrize("source, expected", [
    ("import sys\nsys.path.insert(0, str(Path.home()))\n", "Path.home()"),
    ("import sys, os\nd = os.path.expanduser('~/x')\nsys.path.insert(0, d)\n", "expanduser"),
    ("import sys, os\nd = os.environ.get('USERPROFILE')\nsys.path.append(d)\n", "env USERPROFILE"),
    ("import sys\nfrom pathlib import Path\nR = Path(__file__).resolve().parents[1]\n"
     "def f():\n    g = R.parent / 'bin'\n    sys.path.insert(0, str(g))\n",
     "climbs 1 level(s) above the tree root"),
    ("import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parents[2]))\n",
     "climbs 1 level(s) above the tree root"),
    ("import sys\nfrom pathlib import Path\nD: Path = Path.home()\nsys.path.insert(0, str(D))\n",
     "Path.home()"),
    ("import sys\nfrom pathlib import Path\nh = str(Path.home())\n"
     "sys.path.insert(0, h + '/scripts')\n", "Path.home()"),
    ("import sys\nsys.path.insert(0, '/opt/company/plugins')\n",
     "absolute literal '/opt/company/plugins'"),
    ("import sys\nfrom pathlib import Path\nR = Path(__file__).resolve().parents[1]\n"
     "for d in (R / 'bin', R.parent / 'docs'):\n    sys.path.insert(0, str(d))\n",
     "climbs 1 level(s) above the tree root"),
    ("import sys\nfrom pathlib import Path\nh = Path.home()\n"
     "sys.path.insert(0, f'{h}/scripts')\n", "Path.home()"),
])
def test_path_provenance_is_followed_through_bindings(source, expected, fake_tree):
    found = [f for f in scan_source(source, "bin/x.py", fake_tree) if f.kind == "path"]
    assert [f.why for f in found] == [expected]


def test_function_local_binding_does_not_taint_the_module_scope(fake_tree):
    src = ("import sys\nfrom pathlib import Path\nD = Path(__file__).parent\n"
           "def unrelated():\n    D = Path.home()\n    return D\n"
           "sys.path.insert(0, str(D))\n")
    assert scan_source(src, "bin/x.py", fake_tree) == []


def test_in_tree_sys_path_is_not_flagged(fake_tree):
    src = ("import sys\nfrom pathlib import Path\nR = Path(__file__).resolve().parents[1]\n"
           "sys.path.insert(0, str(R / 'bin'))\nsys.path.insert(0, str(R))\n")
    assert scan_source(src, "bin/x.py", fake_tree) == []


def test_function_local_import_is_scanned(fake_tree):
    src = "def f():\n    import not_shipped_anywhere\n"
    (f,) = scan_source(src, "execution/x.py", fake_tree)
    assert (f.symbol, f.kind, f.name) == ("f", "import", "not_shipped_anywhere")


def test_dotted_import_must_resolve_every_segment(fake_tree):
    src = ("from execution.unshipped_module import helper\n"
           "from execution.doctor import main\nimport execution.doctor\n")
    assert _kinds(scan_source(src, "bin/x.py", fake_tree)) == {
        ("import", "execution.unshipped_module")}


def test_relative_imports_are_resolved_against_the_package(fake_tree):
    src = "from .sibling import a\nfrom . import sibling\nfrom .missing import b\n"
    assert _kinds(scan_source(src, "pkg/mod.py", fake_tree)) == {("import", ".missing")}


def test_aliased_sys_path_insert_is_caught(fake_tree):
    src = ("import sys as _sys\nfrom pathlib import Path\n"
           "_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n")
    assert _kinds(scan_source(src, "scouts/x.py", fake_tree)) == {("path", "sys.path.insert")}


def test_from_imported_path_helpers_are_canonicalized(fake_tree):
    src = ("from importlib.util import spec_from_file_location as sffl\nfrom pathlib import Path\n"
           "sffl('m', str(Path.home() / 'm.py'))\n")
    assert _kinds(scan_source(src, "bin/x.py", fake_tree)) == {
        ("path", "importlib.util.spec_from_file_location")}


def test_literal_dynamic_imports_are_resolved(fake_tree):
    src = ("import importlib\nfrom importlib import import_module as im\n"
           "importlib.import_module('pkg.withheld')\nim('pkg.sibling')\n"
           "__import__('json')\nimportlib.import_module(name_from_config)\n")
    assert _kinds(scan_source(src, "bin/x.py", fake_tree)) == {("import", "pkg.withheld")}


def test_package_existence_does_not_mask_a_withheld_submodule(fake_tree):
    src = ("from pkg import withheld\nfrom pkg import sibling, a, VERSION\n"
           "from . import withheld as w2\n")
    assert _kinds(scan_source(src, "pkg/mod.py", fake_tree)) == {
        ("import", "pkg.withheld"), ("import", ".withheld")}


def test_star_import_or_module_getattr_accepts_any_name(fake_tree):
    (fake_tree / "pkg" / "dyn.py").write_text("def __getattr__(name):\n    return 1\n",
                                             encoding="utf-8")
    src = "from pkg.dyn import anything\n"
    assert scan_source(src, "bin/x.py", fake_tree) == []


def test_imported_path_constant_is_resolved_in_its_defining_module(fake_tree):
    """`from pkg.paths import SCRIPT` where pkg/paths.py builds SCRIPT above the tree root
    (the wiki_compile / wiki_link -> aggregate._VAULT_HEALTH_SCRIPT shape)."""
    (fake_tree / "pkg" / "paths.py").write_text(
        "from pathlib import Path\n"
        "SCRIPT = Path(__file__).resolve().parents[2] / 'Knowledge' / 'vault_health.py'\n"
        "INSIDE = Path(__file__).resolve().parents[1] / 'bin' / 'tool.py'\n", encoding="utf-8")
    (fake_tree / "pkg" / "facade.py").write_text("from .paths import SCRIPT, INSIDE\n",
                                                encoding="utf-8")
    src = ("import importlib.util\n"
           "def load():\n    from pkg.facade import SCRIPT\n"
           "    return importlib.util.spec_from_file_location('v', SCRIPT)\n"
           "def load_inside():\n    from pkg.paths import INSIDE\n"
           "    return importlib.util.spec_from_file_location('t', INSIDE)\n")
    found = scan_source(src, "bin/x.py", fake_tree)
    assert [(f.symbol, f.kind, f.why) for f in found] == [
        ("load", "path", "climbs 1 level(s) above the tree root")]


def test_script_directory_and_stdlib_and_declared_deps_resolve(fake_tree):
    (fake_tree / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pip-audit>=2", "requests"]\n', encoding="utf-8")
    src = "import inside_module\nimport json\nimport pip_audit\nimport requests.adapters\n"
    assert scan_source(src, "bin/x.py", fake_tree) == []


def test_registry_flags_stale_and_silent_entries():
    entry = {"file": "bin/x.py", "symbol": "f", "imports": ["gone"], "path_calls": [],
             "target": "t", "reason": "r", "degrade": "d", "visibility": "silent",
             "surface": ""}
    problems = registry_problems([], [entry])
    assert any("silent" in p for p in problems)
    assert any("stale registry entry" in p for p in problems)


def test_doctor_half_requires_warn_rows_and_a_clean_exit():
    sites = [{"file": "execution/doctor.py", "symbol": "f", "doctor_row": "container-services"}]
    ok = "[WARN] container-services: fleet_probe import failed\nSummary: OK=1 WARN=1 FAIL=0\n"
    assert doctor_problems(ok, 0, sites) == []
    green = "[OK] container-services: all reachable\n"
    assert doctor_problems(green, 0, sites)
    assert doctor_problems("", 0, sites)
    assert doctor_problems(ok, 1, sites)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--check-doctor":
        print("usage: test_import_boundary.py --check-doctor <doctor-output-file> <exit-code>",
              file=sys.stderr)
        return 2
    output = Path(argv[1]).read_text(encoding="utf-8", errors="replace")
    kind = tree_kind(TREE_ROOT)
    sites = [s for s in load_registry(TREE_ROOT) if _applies(s, kind)]
    problems = doctor_problems(output, int(argv[2]), sites)
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print("import boundary: every registered doctor row reads WARN under an empty HOME")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
