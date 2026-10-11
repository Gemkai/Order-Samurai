"""No shipped module may reach outside the shipped tree without saying so.

The 2.1.3 scrubber incident: a shipped module put a directory that only exists in the
monorepo (or under the developer's ``~/.claude``) on ``sys.path``, imported from it,
caught the ImportError, and degraded silently. Every public install lost the feature;
doctor stayed green. `test_export_allowlist.py` and `test_export_withheld_callers.py`
close the ``agentica_core`` import graph only, so nothing caught the pattern.

Shipped means what a public install actually gets, never whatever is on disk:

  * a git checkout (the tree root is the toplevel, ``.git`` a directory or a linked
    worktree's file): every file ``git ls-files`` lists;
  * an export (no git): every file ``bin/extract_public.py`` lists in ``.export-tree``;
  * neither (an unzipped buyer install): a disk walk that skips dot-directories. This
    fallback is a documented gap (owner decision D3): untracked scratch is scanned and a
    dot-directory source is not. CI always scans a git checkout or an export.

A shipped ``.pth`` file fails the guard: site.py executes its lines at interpreter
start-up, so it is a loader no AST scan can see. So does a shipped ``.py`` that does not
parse, an ``.export-tree`` marker inside a git checkout, and a marker line naming a
missing file. Imports resolve against the shipped set only, so an untracked scratch
module cannot make an unregistered import look in-tree.

The guard AST-walks every shipped ``.py`` (dot-directories included) and flags:

  * a static import (function-local ones included) that resolves to none of: the
    standard library, a dependency declared in ``pyproject.toml``, the test runner, or
    a module inside the shipped tree;
  * an import-path or file loader -- ``sys.path.insert/append/extend``, an assignment to
    ``sys.path`` or a package ``__path__``, ``site.addsitedir``, ``spec_from_file_location``
    (and its ``submodule_search_locations``), ``run_path``, ``SourceFileLoader``,
    ``SourcelessFileLoader``, ``ExtensionFileLoader``, ``zipimporter`` -- whose path is
    derived from outside the tree: a
    ``.parent``/``parents[n]`` chain that climbs above the tree root, ``Path.home()``,
    ``expanduser``, or a ``HOME``/``USERPROFILE`` environment read. Provenance is
    followed through simple name assignments, so ``d = Path.home() / "x";
    sys.path.insert(0, str(d))`` is caught, not just the literal call;
  * a Python subprocess (``subprocess.run/call/check_call/check_output/Popen`` whose argv
    is a list starting with ``sys.executable`` or a ``python*`` literal, or whose
    ``executable=`` is Python): options are parsed as CPython does (``-Ic``, ``-mmod``,
    ``-W``/``-X`` values, ``--``); the script is graded like a loader path, ``-m <module>``
    like an import, and literal ``-c`` code is scanned as source of the calling function;
  * a loader whose path cannot be resolved (a parameter, ``*args``/``**kwargs``, an
    unmodelled call, a non-HOME environment read, an ``env or default`` choice, an unknown
    path component, alternatives that differ) or a dynamic import of a non-literal name.
    These fail closed: the entry must list the call in ``path_calls`` and carry
    ``"provenance": "unresolved"``. Loader arguments are read by position or keyword.

Provenance follows the code: in-tree means derived from ``__file__`` without leaving the
root. ``os.environ.get(K, default)`` is graded by its in-code default (the W0 rule for an
operator override). A name bound more than once (``global`` rebinding included) takes all
its values -- unresolved if any is, outside if any is -- with self-updates (``p =
p.parent``) applied to the others; a path built as a string is resolved as a whole. A
module attribute this file reassigns (``mod.X = v``, ``setattr(mod, ...)``) takes every
assigned value. A list argv is trusted only while it is never changed in place and never
escapes (aliased, stored, passed to a call, used by a nested function): a tail added with
``append``/``extend``/``+=`` is unknown, anything else makes a possible Python argv
unresolved. ``importlib.import_module``, ``__import__`` and ``runpy.run_module`` of a
literal are imports. Three shipped layout helpers (``_TREE_ROOT_HELPERS``) are modelled as
returning the tree root for a default or in-tree start, which they do in every shipped
tree. A shipped path that resolves outside the tree (a symlink) fails.

Known gaps, by design: ``shell=True`` subprocesses, a subprocess argv that is not a list
literal (or a name bound to one), non-Python executables, ``os.exec*``/``os.spawn*``/
``asyncio``/``multiprocessing``/``pty`` process APIs and ``os.system``/``os.popen`` (in
either tree none runs a file from outside it), imports made by third-party code, reflection
(``getattr(module, name)``, ``globals()``), ``exec``/``eval``/``compile`` of file contents,
native libraries (``ctypes``), environment variables a child Python reads
(``PYTHONPATH``, ``PYTHONHOME``, ``PYTHONSTARTUP``; in either tree only tests set them), and
flow beyond what is modelled: a value changed from another module (a test's
``monkeypatch``) or through ``nonlocal`` is not seen.

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
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

import pytest

TREE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "config/out_of_package_imports.json"
FIXTURE_REL = "tests/falsifiability_fixtures/import_boundary"
# Written by bin/extract_public.py: the export's shipped-file list (it has no git index).
EXPORT_MARKER = ".export-tree"
_GIT_TIMEOUT_S = 60

# Shipped .py files that are data for a test, not code paths (the negative fixtures are
# passed to scan_source() explicitly), and bytecode caches.
_SKIP_PARTS = {"falsifiability_fixtures", "__pycache__"}


def _skipped(rel: str | PurePosixPath) -> bool:
    return any(part in _SKIP_PARTS for part in PurePosixPath(rel).parts)

# Test-only third-party modules the exported suite needs (requirements-dev.txt).
_DEV_MODULES = {"pytest", "_pytest"}

# Canonical dotted names (import aliases are resolved first, so `import sys as _sys;
# _sys.path.insert(...)` and `from runpy import run_path` both land here), each with the
# position and keyword of its path (or module-name) argument.
_PATH_CALLS = {
    "sys.path.insert": (1, None),
    "sys.path.append": (0, None),
    "site.addsitedir": (0, "sitedir"),
    "importlib.util.spec_from_file_location": (1, "location"),
    "runpy.run_path": (0, "path_name"),
    "importlib.machinery.SourceFileLoader": (1, "path"),
    "importlib.machinery.SourcelessFileLoader": (1, "path"),
    "importlib.machinery.ExtensionFileLoader": (1, "path"),
    "zipimport.zipimporter": (0, "archivepath"),
}
# Calls and assignments that add a whole sequence of entries to an import path.
_PATH_EXTENDS = {"sys.path.extend"}
_DYNAMIC_IMPORTS = {"importlib.import_module": (0, "name"), "__import__": (0, "name"),
                    "importlib.__import__": (0, "name"), "builtins.__import__": (0, "name"),
                    "runpy.run_module": (0, "mod_name")}
_HOME_ENV = {"HOME", "USERPROFILE"}
_SUBPROCESS_CALLS = {"subprocess.run", "subprocess.call", "subprocess.check_call",
                     "subprocess.check_output", "subprocess.Popen"}
_PYTHON_EXE = re.compile(r"python(\d+(\.\d+)*)?(\.exe)?", re.IGNORECASE)
# CPython option letters that take a value (attached or as the next argument); `c` and `m`
# end option parsing with code or a module.
_PY_OPT_VALUE = {"W", "X"}
_PY_LONG_OPT_VALUE = {"--check-hash-based-pycs"}
# In-place list mutations: the first group can change any element, the second only adds a
# tail.
_LIST_POSITIONAL = {"insert", "pop", "remove", "clear", "sort", "reverse", "__setitem__",
                    "__delitem__"}
_LIST_TAIL = {"append", "extend", "__iadd__"}
_ABSENT = object()
# Shipped layout helpers that return the nearest ancestor of `start` (default: their own
# file) holding agentica_core/: the tree root for any in-tree start in a shipped tree
# (Governance/ only inside the monorepo). runtime_paths' GOVERNANCE_ROOT override is graded
# like any `.get(K, default)`, by its default. Matched by the file the call resolves to,
# however it was imported.
_TREE_ROOT_HELPERS = {
    ("execution/claude_runtime_target.py", "governance_root"),
    ("execution/runtime_paths.py", "governance_root"),
    ("tests/_layout.py", "governance_root"),
}
UNRESOLVED = "unresolved"


# --------------------------------------------------------------------------- shipped set


@dataclass
class Shipped:
    """The files a public install of a tree gets, and how that was decided."""
    source: str                    # "git" | "export-tree" | "disk"
    files: set[str]                # tree-relative posix paths, every file type
    problems: list[str] = field(default_factory=list)  # each fails the production test


def _git_env() -> dict[str, str]:
    # A hook or a parent git process may export GIT_DIR / GIT_INDEX_FILE; the tree's own
    # repository is the only one that counts.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(tree_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(tree_root), *args],
                          capture_output=True, timeout=_GIT_TIMEOUT_S, env=_git_env())


def _git_files(tree_root: Path) -> tuple[set[str] | None, str]:
    """(tracked files, "") when `tree_root` is a git toplevel; (None, "") when it is not a
    checkout at all; (None, why) when it is one but git cannot list it (fails closed)."""
    if not (tree_root / ".git").exists():
        return None, ""
    try:
        top = _git(tree_root, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return None, f"git rev-parse failed: {top.stderr.decode(errors='replace').strip()}"
        if Path(top.stdout.decode().strip()).resolve() != tree_root.resolve():
            return None, f".git exists but the git toplevel is {top.stdout.decode().strip()}"
        listed = _git(tree_root, "ls-files", "-z")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git unavailable ({type(exc).__name__})"
    if listed.returncode != 0:
        return None, f"git ls-files failed: {listed.stderr.decode(errors='replace').strip()}"
    return {p for p in listed.stdout.decode("utf-8").split("\0") if p}, ""


def _marker_files(tree_root: Path) -> tuple[set[str], list[str]]:
    files: set[str] = set()
    problems: list[str] = []
    text = (tree_root / EXPORT_MARKER).read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        rel = PurePosixPath(entry)
        if rel.is_absolute() or ".." in rel.parts or re.match(r"^[A-Za-z]:", entry) \
                or "\\" in entry:
            problems.append(f"{EXPORT_MARKER}:{n}: {entry!r} is not a tree-relative path")
            continue
        files.add(rel.as_posix())
    files.discard(EXPORT_MARKER)
    return files, problems


def _disk_files(tree_root: Path) -> set[str]:
    """Owner decision D3: an install with neither git nor a marker falls back to the disk,
    skipping dot-directories and dot-files (runtime scratch such as .tmp/ and .venv/)."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(tree_root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = Path(dirpath).relative_to(tree_root)
        out.update((rel_dir / f).as_posix() for f in filenames if not f.startswith("."))
    return out


def shipped_files(tree_root: Path) -> Shipped:
    """Every file a public install of `tree_root` ships (see the module docstring)."""
    problems: list[str] = []
    tracked, git_error = _git_files(tree_root)
    if git_error:
        return Shipped("git", set(), [f"cannot list the tracked files of {tree_root}: "
                                      f"{git_error}"])
    marker = tree_root / EXPORT_MARKER
    if tracked is not None:
        source, files = "git", tracked
        if marker.exists():
            problems.append(f"{EXPORT_MARKER} is inside a git checkout: the export marker "
                            f"is never ported or committed -- delete it")
    elif marker.is_file():
        source = "export-tree"
        files, marker_problems = _marker_files(tree_root)
        problems += marker_problems
    else:
        source, files = "disk", _disk_files(tree_root)
    missing = sorted(f for f in files if not (tree_root / f).exists())
    problems += [f"{f}: shipped ({source}) but missing on disk" for f in missing]
    root = tree_root.resolve()
    problems += [f"{f}: shipped path resolves outside the tree (a symlink); ship the file "
                 f"itself" for f in sorted(set(files) - set(missing))
                 if not (tree_root / f).resolve().is_relative_to(root)]
    problems += [f"{f}: shipped .pth file -- site.py executes it at interpreter start-up, "
                 f"so it can load anything; never ship one"
                 for f in sorted(files) if f.lower().endswith(".pth")]
    return Shipped(source, set(files) - set(missing), problems)


# --------------------------------------------------------------------------- scanning


@dataclass(frozen=True)
class Finding:
    file: str      # tree-relative, posix
    symbol: str    # enclosing def/class qualname, or "<module>"
    kind: str      # "import" | "path" | "parse" (a shipped file that does not parse)
    name: str      # dotted module name, the path call's dotted name, or the parse error
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


def shipped_python_files(tree_root: Path, shipped: Shipped | None = None) -> list[Path]:
    shipped = shipped or shipped_files(tree_root)
    return [tree_root / f for f in sorted(shipped.files)
            if f.endswith(".py") and not _skipped(f)]


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


class _Element(ast.expr):
    """A name bound by unpacking (`a, b = value`, `for a, b in value`): element `index`
    of `value` (of each item of it when `iterated`); index None when not positional."""
    _fields = ("value",)

    def __init__(self, value: ast.AST, index: int | None, iterated: bool):
        super().__init__(value=value)
        self.index, self.iterated = index, iterated


def _bindings(body: list[ast.stmt]) -> dict[str, list[ast.AST]]:
    frame: dict[str, list[ast.AST]] = {}
    for node in _scope_nodes(body):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        iterated = isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension))
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            # `x op= v` is the self-update `x = x op v`.
            targets = [node.target]
            value = ast.BinOp(left=ast.Name(id=node.target.id, ctx=ast.Load()), op=node.op,
                              right=node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets, value = [node.target], node.value
        elif iterated:
            targets, value = [node.target], node.iter
        if value is None:
            continue
        for t in targets:
            if isinstance(t, (ast.Tuple, ast.List)):
                positional = True
                for i, elt in enumerate(t.elts):
                    positional = positional and not isinstance(elt, ast.Starred)
                    for n in _target_names(elt):
                        index = i if positional and n is elt else None
                        frame.setdefault(n.id, []).append(_Element(value, index, iterated))
                continue
            for n in _target_names(t):
                frame.setdefault(n.id, []).append(value)
    return frame


_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
# Calls a list may be passed to without being changed or kept: read-only builtins, the
# subprocess call itself (matched by its last name), and `"sep".join` / `shlex.join`.
_READ_ONLY_CALLS = {"len", "str", "repr", "print", "list", "tuple", "sorted", "bool", "any",
                    "all", "enumerate", "iter", "reversed", "set", "frozenset", "shlex.quote"}
_SINK_NAMES = {"run", "call", "check_call", "check_output", "Popen", "join"}
# Parents under which a list name is only read.
_READ_PARENTS = (ast.Subscript, ast.Attribute, ast.Starred, ast.Compare, ast.BoolOp,
                 ast.UnaryOp, ast.If, ast.While, ast.IfExp, ast.Assert, ast.FormattedValue,
                 ast.JoinedStr, ast.Return, ast.Expr, ast.BinOp, ast.For, ast.AsyncFor,
                 ast.comprehension, ast.AugAssign)


def _name_use(parent: ast.AST, child: ast.Name) -> str | None:
    """"escape" when `child` (a loaded name) is handed somewhere that may keep or change
    it -- aliased, stored in a container, passed to an arbitrary call; None for a read."""
    if isinstance(parent, ast.Call):
        if child is parent.func:
            return None
        dotted = _dotted(parent.func)
        last = parent.func.attr if isinstance(parent.func, ast.Attribute) else dotted
        if dotted in _READ_ONLY_CALLS or last in _SINK_NAMES:
            return None
        return "escape"
    if isinstance(parent, ast.keyword):
        return None if parent.arg == "args" else "escape"
    return None if isinstance(parent, _READ_PARENTS) else "escape"


def _nested_scopes(body: list[ast.stmt]):
    for node in body:
        if isinstance(node, _NESTED):
            yield node
    for node in _scope_nodes(body):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED):
                yield child


def _list_uses(body: list[ast.stmt]) -> tuple[dict[str, str], list[ast.AST]]:
    """How one scope (with the free names of the scopes nested in it) uses each name, for
    argv lists: "positional" (`x[i] = v`, `del x[i]`, insert/pop/remove/...), "tail"
    (append/extend/`+=`), "escape" (see _name_use); and every value stored into a list."""
    kinds: dict[str, str] = {}
    stores: list[ast.AST] = []

    def mark(name: str, kind: str) -> None:
        if kinds.get(name) not in ("positional", "escape"):
            kinds[name] = kind

    for node in _scope_nodes(body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) \
                else [node.target]
            for target in targets:
                for t in ast.walk(target):
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                        mark(t.value.id, "positional")
                        if getattr(node, "value", None) is not None:
                            stores.append(node.value)
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            mark(node.target.id, "tail" if isinstance(node.op, ast.Add) else "positional")
            stores.append(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name):
            if node.func.attr in _LIST_POSITIONAL | _LIST_TAIL:
                mark(node.func.value.id,
                     "positional" if node.func.attr in _LIST_POSITIONAL else "tail")
                stores.extend(node.args)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                if _name_use(node, child) == "escape":
                    mark(child.id, "escape")
    for nested in _nested_scopes(body):
        inner = [ast.Expr(nested.body)] if isinstance(nested, ast.Lambda) else nested.body
        inner_kinds, inner_stores = _list_uses(inner)
        local = set(_bindings(inner))
        if not isinstance(nested, ast.ClassDef):
            local |= {a.arg for a in ast.walk(nested.args) if isinstance(a, ast.arg)}
        for name, kind in inner_kinds.items():
            if name not in local:
                mark(name, kind)
        stores.extend(inner_stores)
    return kinds, stores


def _add_global_bindings(module: ast.Module, frame: dict[str, list[ast.AST]]) -> None:
    """A function that declares `global X` and assigns X rebinds the module-level X."""
    for fn in ast.walk(module):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        declared = {n for node in _scope_nodes(fn.body) if isinstance(node, ast.Global)
                    for n in node.names}
        if declared:
            local = _bindings(fn.body)
            for name in declared & set(local):
                frame.setdefault(name, []).extend(local[name])


def _target_names(target: ast.AST) -> list[ast.Name]:
    """Names an assignment target rebinds: `obj.attr = v` and `d[k] = v` rebind none."""
    if isinstance(target, ast.Name):
        return [target]
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _target_names(elt)]
    return []


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
        self.mutations: list[dict[str, str]] = []     # per frame: name -> list use (_list_uses)
        self.stores: list[list[ast.AST]] = []         # per frame: values stored into lists
        self.overrides: dict[str, _Resolved] = {}     # name -> value while applying an update
        # "mod.ATTR" (or "mod.*" for a computed name) -> values this file assigns to it
        self.attr_writes: dict[str, list[ast.AST]] = {}

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
        self._collect_attr_writes(node)
        self._push(node.body)
        _add_global_bindings(node, self.frames[-1])
        self.generic_visit(node)
        self._pop()

    def _push(self, body: list[ast.stmt]) -> None:
        self.frames.append(_bindings(body))
        kinds, stores = _list_uses(body)
        self.mutations.append(kinds)
        self.stores.append(stores)

    def _pop(self) -> None:
        self.frames.pop()
        self.mutations.pop()
        self.stores.pop()

    def _collect_attr_writes(self, tree: ast.AST) -> None:
        """`mod.ATTR = v` and `setattr(mod, "ATTR", v)` on an imported module alias."""
        for sub in ast.walk(tree):
            if isinstance(sub, ast.Assign):
                pairs = [(t, sub.value) for t in sub.targets]
            elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)) and sub.value is not None:
                pairs = [(sub.target, sub.value)]
            else:
                pairs = []
            for target, value in pairs:
                for t in ast.walk(target):
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                            and t.value.id in self.aliases:
                        self.attr_writes.setdefault(self._canonical(t), []).append(value)
            if isinstance(sub, ast.Call) and len(sub.args) >= 3 \
                    and _dotted(sub.func).rpartition(".")[2] == "setattr" \
                    and isinstance(sub.args[0], ast.Name) and sub.args[0].id in self.aliases:
                attr = sub.args[1]
                name = attr.value if isinstance(attr, ast.Constant) \
                    and isinstance(attr.value, str) else "*"
                key = f"{self._canonical(sub.args[0])}.{name}"
                self.attr_writes.setdefault(key, []).append(sub.args[2])

    def _list_use(self, name: str) -> str | None:
        """How the list bound to `name` may change before a use: "positional" or "escape"
        (any element may change), "tail" (only appended to), or None."""
        kind = None
        for frame, uses in zip(reversed(self.frames), reversed(self.mutations)):
            found = uses.get(name)
            if found in ("positional", "escape") or (found and kind is None):
                kind = found
            if name in frame:
                break
        return kind

    def _python_stored(self) -> bool:
        """Whether code in scope stores a Python executable into some list."""
        for stores in self.stores:
            for value in stores:
                elts = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
                if any(self._is_python(e) for e in elts):
                    return True
        return False

    def _enter(self, node, name: str) -> None:
        self.scope.append(name)
        self._push(node.body)
        self.generic_visit(node)
        self._pop()
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
        if name in _DYNAMIC_IMPORTS:
            # A literal dynamic import is still an import: `import_module("x.y")`. A name
            # bound only to literals is followed; anything else cannot be resolved.
            arg = _call_arg(node, *_DYNAMIC_IMPORTS[name])
            targets = None if arg is None else (
                [] if arg is _ABSENT else self._literal_strs(arg, 0, frozenset()))
            if targets is None:
                self._unresolved(name, node)
            for target in targets or []:
                self._check_module(target, node)
        arg = _call_arg(node, *_PATH_CALLS[name]) if name in _PATH_CALLS else _ABSENT
        if arg is not _ABSENT:  # absent: the call passes no path (`spec_from_file_location("x")`)
            res = None if arg is None else self._resolve(arg, 0, frozenset())
            if res is None:
                self._unresolved(name, node)
            elif res[0] == "out":
                self.findings.append(Finding(self.rel, self._symbol(), "path", name,
                                             node.lineno, str(res[1])))
            elif name.startswith("sys.path"):
                self.inserted.add(tuple(res[1]))  # type: ignore[arg-type]
        if name == "importlib.util.spec_from_file_location":
            # A package's search path; `**kwargs` that may carry it are unresolved, once.
            locations = _call_arg(node, 99, "submodule_search_locations")
            already = any(f.line == node.lineno and f.name == name for f in self.findings)
            if locations is not _ABSENT and not (locations is None and already) \
                    and not (isinstance(locations, ast.Constant) and locations.value is None):
                self._path_value(name, node, locations)
        if name in _PATH_EXTENDS or (name.endswith(".__path__.extend") and node.args):
            self._path_value(name, node, node.args[0] if node.args else None)
        elif name.rpartition(".")[0].endswith("__path__") and \
                name.rpartition(".")[2] in ("append", "insert") and node.args:
            # A package's __path__ is an import path too.
            self._path_value(name, node, ast.List(elts=[node.args[-1]], ctx=ast.Load()))
        if name in _SUBPROCESS_CALLS:
            self._python_subprocess(name, node)
        self.generic_visit(node)

    # sys.path / __path__ written as a whole ------------------------------------------

    def visit_Assign(self, node):  # noqa: N802
        for target in node.targets:
            self._path_target(target, node.value, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node):  # noqa: N802
        self._path_target(node.target, node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):  # noqa: N802
        if node.value is not None:
            self._path_target(node.target, node.value, node)
        self.generic_visit(node)

    def _path_target(self, target: ast.AST, value: ast.AST, node: ast.AST) -> None:
        """`sys.path = v`, `sys.path[:0] = v`, `sys.path += v`, `pkg.__path__ = v`."""
        base = target.value if isinstance(target, ast.Subscript) else target
        name = self._canonical(base)
        if name == "sys.path" or name.endswith(".__path__") or name == "__path__":
            if isinstance(target, ast.Subscript) and not isinstance(target.slice, ast.Slice):
                value = ast.List(elts=[value], ctx=ast.Load())  # sys.path[0] = one entry
            self._path_value(name, node, value)

    def _path_value(self, name: str, node: ast.AST, value: ast.AST | None) -> None:
        """Each entry a new import-path value adds is graded like a sys.path.insert path;
        entries kept from sys.path itself (a saved copy, a filter over it) add nothing."""
        entries = None if value is None else self._path_entries(value, 0)
        if entries is None:
            self._unresolved(name, node)
            return
        for entry in entries:
            res = self._resolve(entry, 0, frozenset())
            if res is None:
                self._unresolved(name, node)
            elif res[0] == "out":
                self.findings.append(Finding(self.rel, self._symbol(), "path", name,
                                             node.lineno, str(res[1])))
            elif name.startswith("sys.path"):
                self.inserted.add(tuple(res[1]))  # type: ignore[arg-type]

    def _path_entries(self, node: ast.AST, depth: int) -> list[ast.AST] | None:
        if depth > 8:
            return None
        if self._is_sys_path_copy(node, depth):
            return []
        if isinstance(node, (ast.List, ast.Tuple)):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                return None
            return list(node.elts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._path_entries(node.left, depth + 1)
            right = self._path_entries(node.right, depth + 1)
            return None if left is None or right is None else left + right
        if isinstance(node, ast.Name):
            values = self._lookup(node.id)
            out: list[ast.AST] = []
            for v in values:
                got = None if _mentions(v, node.id) else self._path_entries(v, depth + 1)
                if got is None:
                    return None
                out += got
            return out if values else None
        return None

    def _is_sys_path_copy(self, node: ast.AST, depth: int) -> bool:
        """`list(sys.path)`, `sys.path.copy()`, `sys.path[:]`, `[p for p in sys.path ...]`,
        or a name bound only to one of those."""
        if self._canonical(node) == "sys.path":
            return True
        if isinstance(node, ast.Call) and node.args and self._canonical(node.func) in {
                "list", "tuple"}:
            return self._is_sys_path_copy(node.args[0], depth + 1)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "copy":
            return self._canonical(node.func.value) == "sys.path"
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            return self._canonical(node.value) == "sys.path"
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)) and len(node.generators) == 1:
            gen = node.generators[0]
            return isinstance(node.elt, ast.Name) and isinstance(gen.target, ast.Name) \
                and node.elt.id == gen.target.id and self._is_sys_path_copy(gen.iter, depth + 1)
        if isinstance(node, ast.Name) and depth < 8:
            values = self._lookup(node.id)
            return bool(values) and all(self._is_sys_path_copy(v, depth + 1) for v in values)
        return False

    def _unresolved(self, name: str, node: ast.AST) -> None:
        self.findings.append(Finding(self.rel, self._symbol(), "path", name, node.lineno,
                                     UNRESOLVED))

    def _check_module(self, target: str, node: ast.AST) -> None:
        if self.resolver is None:
            return
        if target.startswith("."):
            level = len(target) - len(target.lstrip("."))
            ok = not self.resolver.missing_relative(target.lstrip(".") or None, level,
                                                    [], self.file_parts)
        else:
            ok = self.resolver.resolves(target, self.file_parts)
        if not ok:
            self.findings.append(
                Finding(self.rel, self._symbol(), "import", target, node.lineno))

    def _literal_strs(self, node: ast.AST, depth: int, seen: frozenset) -> list[str] | None:
        """Every string `node` can evaluate to, following simple name bindings (a
        `for x in ("a", "b")` binding yields both); None when any value is not a literal."""
        if depth > 16:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, _Element):
            items = self._unpacked(node, depth)
            out = []
            for item in items or [None]:
                got = None if item is None else self._literal_strs(item, depth + 1, seen)
                if got is None:
                    return None
                out += got
            return out
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            out: list[str] = []
            for elt in node.elts:
                got = self._literal_strs(elt, depth + 1, seen)
                if got is None:
                    return None
                out += got
            return out
        if isinstance(node, ast.Name) and node.id not in seen:
            values = self._lookup(node.id)
            if not values:
                return None
            out = []
            for value in values:
                got = self._literal_strs(value, depth + 1, seen | {node.id})
                if got is None:
                    return None
                out += got
            return out
        return None

    def _unpacked(self, node: "_Element", depth: int) -> list[ast.AST] | None:
        """The expressions an unpacked name can take: element `index` of the value (of each
        item of it when iterated); None when that cannot be read off a display."""
        if node.index is None:
            return None
        if node.iterated:
            items = self._displays(node.value, depth + 1)
        else:
            items = [node.value]
            if isinstance(node.value, ast.Name):
                items = [v for v in self._lookup(node.value.id)] or None
        out = []
        for item in items or [None]:
            if not isinstance(item, (ast.Tuple, ast.List)) or len(item.elts) <= node.index \
                    or any(isinstance(e, ast.Starred) for e in item.elts):
                return None
            out.append(item.elts[node.index])
        return out

    def _displays(self, node: ast.AST, depth: int) -> list[ast.AST] | None:
        """The items of an iterable display, following simple name bindings."""
        if depth > 16:
            return None
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return list(node.elts)
        if isinstance(node, ast.Name):
            values = self._lookup(node.id)
            out: list[ast.AST] = []
            for value in values:
                got = self._displays(value, depth + 1)
                if got is None:
                    return None
                out += got
            return out if values else None
        return None

    # python subprocesses -------------------------------------------------------------

    def _argv_lists(self, node: ast.AST | None, depth: int = 0) -> list[list[ast.AST]]:
        """The literal argv lists `node` may be: a list/tuple display, a name bound to one,
        or `[...] + rest` (an unknown tail becomes a Starred placeholder)."""
        if node is None or depth > 8:
            return []
        if isinstance(node, (ast.List, ast.Tuple)):
            return [list(node.elts)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            heads = self._argv_lists(node.left, depth + 1)
            tails = self._argv_lists(node.right, depth + 1) or [[ast.Starred(value=node.right)]]
            return [h + t for h in heads for t in tails]
        if isinstance(node, ast.Name):
            # A self-update (`cmd += [...]`) is a tail, handled by _list_uses.
            return [argv for value in self._lookup(node.id) if not _mentions(value, node.id)
                    for argv in self._argv_lists(value, depth + 1)]
        return []

    def _is_python(self, node: ast.AST, depth: int = 0) -> bool:
        if depth > 8:
            return False
        if isinstance(node, ast.Call) and self._canonical(node.func) in {"str", "os.fspath"} \
                and node.args:
            return self._is_python(node.args[0], depth + 1)
        if self._canonical(node) == "sys.executable":
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return bool(_PYTHON_EXE.fullmatch(re.split(r"[\\/]", node.value)[-1]))
        if isinstance(node, ast.Name):
            return any(self._is_python(v, depth + 1) for v in self._lookup(node.id))
        return False

    def _python_subprocess(self, name: str, node: ast.Call) -> None:
        """`subprocess.run([sys.executable, <script> | -m <module> | -c <code>, ...])`."""
        for kw in node.keywords:
            if kw.arg == "shell" and not (isinstance(kw.value, ast.Constant)
                                          and kw.value.value is False):
                return  # documented gap: a shell command line is not parsed
        argv = _call_arg(node, 0, "args")
        executable = _call_arg(node, 2, "executable")  # Popen's executable= replaces argv[0]
        python_exe = executable not in (None, _ABSENT) and self._is_python(executable)
        use = self._list_use(argv.id) if isinstance(argv, ast.Name) else None
        lists = self._argv_lists(argv if argv is not _ABSENT else None)
        if use in ("positional", "escape") or (isinstance(argv, ast.Name)
                                               and any(not e for e in lists)):
            # Any element may have changed before the run (`cmd[0] = ...`, an alias, a
            # callee, a closure, or a list built from empty): unresolved when it can be a
            # Python argv; a non-Python argv stays the documented boundary.
            if python_exe or any(e and self._is_python(e[0]) for e in lists) \
                    or self._python_stored():
                self._unresolved(name, node)
            return
        mutation = use
        # A list reached through a name may be extended elsewhere (`cmd.append(script)`,
        # `cmd += [...]`), so running out of arguments there is unresolved, not "stdin".
        bound = not isinstance(argv, (ast.List, ast.Tuple))
        for elts in lists:
            if elts and (python_exe or self._is_python(elts[0])):
                tail = [ast.Starred(value=argv)] if mutation == "tail" else []
                self._python_argv(name, node, elts[1:] + tail, bound)

    def _python_argv(self, name: str, node: ast.Call, rest: list[ast.AST],
                     bound: bool) -> None:
        """Parse a Python argv the way CPython does: short options may be combined
        (`-Ic`), `c`/`m` take the rest of the option or the next argument, `W`/`X` take a
        value, `--` ends the options; the first other argument is the script."""
        i = 0
        while i < len(rest):
            arg = rest[i]
            if isinstance(arg, ast.Starred):
                self._unresolved(name, node)  # options or script unknown
                return
            opt = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                else None
            if opt == "--":  # end of options: the next argument is the script
                script = rest[i + 1] if i + 1 < len(rest) else None
                if script is not None and not (isinstance(script, ast.Constant)
                                               and script.value == "-"):
                    self._python_script(name, node, script)
                return
            if opt is not None and opt.startswith("--"):
                i += 2 if opt in _PY_LONG_OPT_VALUE else 1
                continue
            if opt is not None and opt.startswith("-") and opt != "-":
                step = 1
                for j, letter in enumerate(opt[1:], 1):
                    if letter in "cm":
                        attached = opt[j + 1:]
                        value = ast.Constant(attached) if attached else (
                            rest[i + 1] if i + 1 < len(rest) else None)
                        self._python_operand(name, node, letter, value)
                        return
                    if letter in _PY_OPT_VALUE:
                        step = 1 if opt[j + 1:] else 2
                        break
                i += step
                continue
            if opt == "-":
                return  # program read from stdin: no file is loaded
            self._python_script(name, node, arg)
            return
        if bound:
            self._unresolved(name, node)

    def _python_script(self, name: str, node: ast.Call, arg: ast.AST) -> None:
        res = self._resolve(arg, 0, frozenset())
        if res is None:
            self._unresolved(name, node)
        elif res[0] == "out":
            self.findings.append(Finding(self.rel, self._symbol(), "path", name,
                                         node.lineno, str(res[1])))

    def _python_operand(self, name: str, node: ast.Call, letter: str,
                        value: ast.AST | None) -> None:
        """`-m <module>` is an import; `-c <code>` is scanned as source."""
        texts = None if value is None or isinstance(value, ast.Starred) \
            else self._literal_strs(value, 0, frozenset())
        if texts is None:
            self._unresolved(name, node)
        elif letter == "m":
            for module in texts:
                self._check_module(module, node)
        else:
            for code in texts:
                self._inline_code(name, node, code)

    def _inline_code(self, name: str, node: ast.Call, code: str) -> None:
        """`python -c <literal>`: scan the code as source of the calling function."""
        try:
            tree = ast.parse(code)
        except (SyntaxError, ValueError):
            self._unresolved(name, node)
            return
        inner = _Scanner(self.rel, self.resolver, self.locator, self.depth)
        inner.scope = list(self.scope)
        inner.visit(tree)
        self.findings += [replace(f, line=node.lineno) for f in inner.findings]

    def _resolve(self, node: ast.AST, depth: int, seen: frozenset) -> _Resolved:
        """Where the path expression `node` points, following simple name bindings."""
        if depth > 16:
            return None
        if isinstance(node, _Element):
            items = self._unpacked(node, depth)
            if items is None:
                return None
            return _either([self._resolve(item, depth + 1, seen) for item in items])
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
            if node.id in self.overrides:
                return self.overrides[node.id]
            if node.id in seen:
                return None
            values = self._lookup(node.id)
            if not values:
                return (self._imported_constant(self.aliases[node.id])
                        if node.id in self.aliases else None)
            return self._join_bindings(node.id, values, depth, seen)
        if isinstance(node, ast.JoinedStr):
            return self._string_path(list(node.values), depth, seen)
        if isinstance(node, ast.FormattedValue):
            return self._resolve(node.value, depth + 1, seen)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return _either([self._resolve(e, depth + 1, seen) for e in node.elts])
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                return _up(self._resolve(node.value, depth + 1, seen), 1)
            if node.attr in {"resolve", "absolute"}:
                return self._resolve(node.value, depth + 1, seen)
            if isinstance(node.value, ast.Name) and node.value.id in self.aliases \
                    and not self._lookup(node.value.id):
                # `mod.CONST` through an imported module alias: evaluate CONST there, and
                # any value this file assigns to it (`mod.CONST = ...`, `setattr(mod, ...)`).
                dotted = self._canonical(node)
                writes = self.attr_writes.get(dotted, []) + self.attr_writes.get(
                    dotted.rpartition(".")[0] + ".*", [])
                return _either([self._imported_constant(dotted)]
                               + [self._resolve(v, depth + 1, seen) for v in writes])
            return None
        if isinstance(node, ast.IfExp):
            return _either([self._resolve(n, depth + 1, seen) for n in (node.body, node.orelse)])
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            # `a or b` may be either: an environment override in `a` is unresolved.
            return _either([self._resolve(v, depth + 1, seen) for v in node.values])
        if isinstance(node, ast.Subscript) and self._canonical(node.value) == "os.environ":
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in _HOME_ENV:
                return ("out", f"env {key.value}")
            return None  # any other variable: unresolved, never assumed in-tree
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "parents":
            base = self._resolve(node.value.value, depth + 1, seen)
            n = node.slice
            if isinstance(n, ast.Constant) and isinstance(n.value, int):
                return _up(base, n.value + 1)
            return base if base is not None and base[0] == "out" else None
        if isinstance(node, ast.Call):
            return self._resolve_call(node, depth, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._join(self._resolve(node.left, depth + 1, seen), [node.right],
                              depth, seen)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._string_path(_add_operands(node), depth, seen)
        return None

    def _join(self, base: _Resolved, parts: list[ast.AST], depth: int,
              seen: frozenset) -> _Resolved:
        """`base / part / ...`: a literal part descends (an absolute one replaces the path);
        any other part replaces the path when it is a path itself, so it must resolve."""
        for part in parts:
            if base is not None and base[0] == "out":
                return base
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                if _is_absolute_literal(part.value):
                    return self._resolve(part, depth + 1, seen)
                base = None if base is None else _down(base, part.value)
                continue
            texts = self._literal_strs(part, 0, frozenset())
            if texts is not None and base is not None:
                # A name bound only to literals (`for sub in ("a", "b"): d / sub`).
                base = _either([self._resolve(ast.Constant(t), depth + 1, seen)
                                if _is_absolute_literal(t) else _down(base, t) for t in texts])
                continue
            right = self._resolve(part, depth + 1, seen)
            if right is None or right[0] == "out":
                return right
            base = right  # an absolute in-tree path replaces what came before
        return base


    def _is_tree_root_helper(self, dotted: str) -> bool:
        module, _, func = dotted.rpartition(".")
        if not module or self.locator is None:
            return False
        found = self.locator._find(module, self.file_parts)
        return found is not None and (self.locator._rel(found), func) in _TREE_ROOT_HELPERS

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
        _add_global_bindings(tree, self.frames[0])
        kinds, stores = _list_uses(tree.body)
        self.mutations, self.stores = [kinds], [stores]

    def _join_bindings(self, name: str, values: list[ast.AST], depth: int,
                       seen: frozenset) -> _Resolved:
        """Every value a name is bound to, joined (flow is not tracked). A binding that
        updates the name from itself (`p = p.parent`, `d /= "b"`) is applied to the other
        values, twice, which covers straight-line code and a loop body."""
        plain = [v for v in values if not _mentions(v, name)]
        updates = [v for v in values if _mentions(v, name)]
        if not plain:
            return None  # only self-updates: the starting value is unknown
        results = [self._resolve(v, depth + 1, seen | {name}) for v in plain]
        current = results
        for _ in range(2 if updates else 0):
            if any(r is None or r[0] == "out" for r in current):
                break
            current = [self._with_override(name, r, v, depth, seen)
                       for r in current for v in updates]
            results = results + current
        return _either(results)

    def _with_override(self, name: str, value: _Resolved, expr: ast.AST, depth: int,
                       seen: frozenset) -> _Resolved:
        saved = self.overrides.get(name, _ABSENT)
        self.overrides[name] = value
        try:
            return self._resolve(expr, depth + 1, seen)
        finally:
            if saved is _ABSENT:
                del self.overrides[name]
            else:
                self.overrides[name] = saved

    def _string_path(self, parts: list[ast.AST], depth: int, seen: frozenset) -> _Resolved:
        """A path built as a string (`str(ROOT) + "/x"`, `f"{ROOT}/x"`): the first part is
        the base; a literal that starts with a separator descends, one without extends the
        last component (the tree root's sibling is outside); os.sep is a separator; any
        other part is unknown."""
        first = parts[0]
        if isinstance(first, ast.FormattedValue):
            if first.conversion != -1 or first.format_spec is not None:
                return None
            first = first.value
        base = self._resolve(first, depth + 1, seen)
        for part in parts[1:]:
            if base is None or base[0] == "out":
                return base
            if isinstance(part, ast.FormattedValue):
                if part.conversion != -1 or part.format_spec is not None:
                    return None
                part = part.value
            if self._canonical(part) in {"os.sep", "os.path.sep"}:
                texts: list[str] | None = ["/"]
            else:
                texts = self._literal_strs(part, 0, frozenset())
            if texts is None:
                return None
            base = _either([_append_text(base, t) for t in texts])
        return base

    def _resolve_call(self, node: ast.Call, depth: int, seen: frozenset) -> _Resolved:
        fn = node.func
        d = self._canonical(fn)
        if d.endswith("Path.home") or d == "home":
            return ("out", "Path.home()")
        if self._is_tree_root_helper(d):
            # The nearest ancestor of `start` holding agentica_core/: the tree root for an
            # in-tree (or the default) start; anything else stays what `start` is.
            start = _call_arg(node, 0, "start")
            if start is _ABSENT:
                return ("in", ())
            res = None if start is None else self._resolve(start, depth + 1, seen)
            return res if res is None or res[0] == "out" else ("in", ())
        if d.endswith("expanduser"):
            return ("out", "expanduser")
        if d in {"os.environ.get", "os.getenv"} and node.args:
            a = node.args[0]
            if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                return None
            if a.value in _HOME_ENV:
                return ("out", f"env {a.value}")
            # An operator override with an in-code default: grade the default (W0 rule).
            return self._resolve(node.args[1], depth + 1, seen) if len(node.args) > 1 else None
        if isinstance(fn, ast.Attribute) and fn.attr in {"resolve", "absolute"}:
            return self._resolve(fn.value, depth + 1, seen)
        if isinstance(fn, ast.Attribute) and fn.attr in {"with_name", "with_stem",
                                                         "with_suffix"}:
            # A sibling of the same path: same directory, unless the path is the tree root
            # itself, whose sibling is outside.
            base = self._resolve(fn.value, depth + 1, seen)
            if base is None or base[0] == "out":
                return base
            if not base[1]:
                return _up(base, 1)
            arg = node.args[0] if node.args else None
            if fn.attr == "with_name" and isinstance(arg, ast.Constant) \
                    and isinstance(arg.value, str):
                return _down(_up(base, 1), arg.value)
            return base
        if isinstance(fn, ast.Attribute) and fn.attr == "joinpath":
            return self._join(self._resolve(fn.value, depth + 1, seen), list(node.args),
                              depth, seen)
        if d in {"pathlib.Path", "Path", "os.path.join"} and node.args:
            if any(isinstance(a, ast.Starred) for a in node.args):
                return None
            return self._join(self._resolve(node.args[0], depth + 1, seen),
                              list(node.args[1:]), depth, seen)
        if d in {"str", "os.fspath", "os.path.abspath", "os.path.realpath",
                 "os.path.normpath", "os.path.expandvars"} and node.args:
            return self._resolve(node.args[0], depth + 1, seen)
        if d == "os.path.dirname" and node.args:
            return _up(self._resolve(node.args[0], depth + 1, seen), 1)
        return None


def _either(results: list[_Resolved]) -> _Resolved:
    """One of several possible paths: unresolved if any alternative is (it may point
    anywhere); else outside if any is; else the shallowest in-tree alternative, the one a
    later `.parent` or `..` takes out first."""
    if not results or any(r is None for r in results):
        return None
    out = next((r for r in results if r[0] == "out"), None)
    if out is not None:
        return out
    return min(results, key=lambda r: len(r[1]))  # type: ignore[arg-type]


def _append_text(base: _Resolved, text: str) -> _Resolved:
    """`str(base) + text`: text up to its first separator renames the last component (the
    tree root's sibling is outside); the rest descends."""
    if base is None or base[0] == "out":
        return base
    m = re.search(r"[\\/]", text)
    head, tail = (text[:m.start()], text[m.end():]) if m else (text, None)
    if head and not base[1]:
        return _up(base, 1)
    return base if tail is None else _down(base, tail.replace("\\", "/"))


def _add_operands(node: ast.AST) -> list[ast.AST]:
    """`a + b + c` (parsed as `(a + b) + c`) as [a, b, c]."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_operands(node.left) + [node.right]
    return [node]


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def _call_arg(node: ast.Call, position: int, keyword: str | None):
    """The argument at `position` or `keyword`; _ABSENT when the call does not pass one;
    None when `*args`/`**kwargs` may supply it (unresolvable)."""
    for i, arg in enumerate(node.args):
        if isinstance(arg, ast.Starred):
            return None
        if i == position:
            return arg
    for kw in node.keywords:
        if keyword is not None and kw.arg == keyword:
            return kw.value
        if kw.arg is None:
            return None
    return _ABSENT


def _is_absolute_literal(value: str) -> bool:
    return value.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", value))


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
    a withheld submodule (`from agentica_core import automation_health`).

    Existence means membership in the shipped set, never the disk: an untracked scratch
    `bin/hook_registry.py` must not make `import hook_registry` look in-tree."""

    def __init__(self, tree_root: Path, inserted: set[tuple[str, ...]],
                 files: set[str] | None = None):
        self.tree_root = tree_root
        self.third_party = _declared_modules(tree_root) | _DEV_MODULES
        self.roots = [()] + sorted(inserted)
        self.files = shipped_files(tree_root).files if files is None else files
        self.dirs = {"."} | {p.as_posix() for f in self.files
                             for p in PurePosixPath(f).parents if p.as_posix() != "."}
        self._defined: dict[Path, set[str] | None] = {}

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.tree_root).as_posix()

    def is_file(self, path: Path) -> bool:
        return self._rel(path) in self.files

    def is_dir(self, path: Path) -> bool:
        return self._rel(path) in self.dirs

    def _locate(self, base: tuple[str, ...], dotted: str) -> Path | None:
        p = self.tree_root.joinpath(*base, *dotted.split("."))
        if self.is_file(p.with_suffix(".py")):
            return p.with_suffix(".py")
        if self.is_dir(p) and not _skipped(self._rel(p)):
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
        if self.is_dir(module):
            init = module / "__init__.py"
            defined = self._defined_names(init) if self.is_file(init) else set()
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


def scan_tree(tree_root: Path, shipped: Shipped | None = None) -> list[Finding]:
    shipped = shipped or shipped_files(tree_root)
    sources = {p.relative_to(tree_root).as_posix(): p.read_text(encoding="utf-8",
                                                                  errors="replace")
               for p in shipped_python_files(tree_root, shipped)}
    # Pass 1: which in-tree directories does shipped code put on sys.path?
    inserted: set[tuple[str, ...]] = set()
    locator = _ImportResolver(tree_root, set(), shipped.files)
    for rel, src in sources.items():
        inserted |= _scan(src, rel, None, locator).inserted
    resolver = _ImportResolver(tree_root, inserted, shipped.files)
    findings: list[Finding] = []
    for rel, src in sources.items():
        findings.extend(_scan(src, rel, resolver).findings)
    return findings


def _scan(source: str, rel: str, resolver: "_ImportResolver | None",
          locator: "_ImportResolver | None" = None) -> "_Scanner":
    scanner = _Scanner(rel, resolver, locator)
    try:
        tree = ast.parse(source, filename=rel)
    except (SyntaxError, ValueError) as exc:  # ValueError: a NUL byte before 3.12
        # A shipped file that does not parse is never silently dropped (W0b G4).
        line = getattr(exc, "lineno", None) or 0
        scanner.findings.append(Finding(rel, "<module>", "parse", type(exc).__name__, line,
                                        str(getattr(exc, "msg", exc))))
        return scanner
    scanner.visit(tree)
    return scanner


def scan_source(source: str, rel: str, tree_root: Path) -> list[Finding]:
    """Findings for one source file placed at `rel` inside `tree_root`."""
    return _scan(source, rel, _ImportResolver(tree_root, set())).findings


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
    unresolved: dict[tuple[str, str], set[str]] = {}
    for f in findings:
        if f.kind == "parse":
            problems.append(f"{f.file}:{f.line}: shipped file does not parse ({f.name}: "
                            f"{f.why}) -- fix it; a parse error cannot be registered")
            continue
        g = grouped.setdefault(f.key(), {"import": set(), "path": set()})
        g[f.kind].add(f.name)
        if f.kind == "path" and f.why == UNRESOLVED:
            unresolved.setdefault(f.key(), set()).add(f.name)
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
        provenance = site.get("provenance")
        if provenance not in (None, UNRESOLVED):
            problems.append(f"{key}: unknown provenance {provenance!r} (expected "
                            f"{UNRESOLVED!r} or no field)")
        actual = grouped.get(key)
        if actual is None:
            problems.append(f"{key}: stale registry entry -- no such out-of-package site")
            continue
        for kind, field in (("import", "imports"), ("path", "path_calls")):
            declared = set(site[field])
            if declared - actual[kind]:
                problems.append(f"{key}: stale {field} {sorted(declared - actual[kind])}")
        if key in unresolved and provenance != UNRESOLVED:
            problems.append(f"{key}: {sorted(unresolved[key])} load a path that cannot be "
                            f"resolved -- the entry needs \"provenance\": \"{UNRESOLVED}\" "
                            f"and a target saying what it loads")
        elif key not in unresolved and provenance == UNRESOLVED:
            problems.append(f"{key}: stale provenance {UNRESOLVED!r} -- every loader path at "
                            f"this site now resolves")
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
            hint = (f" (unresolved: {sorted(unresolved[key])})" if key in unresolved else "")
            problems.append(f"{key} (lines {lines}): unregistered out-of-package "
                            f"imports {sorted(extra_i)} path calls {sorted(extra_p)}{hint}")
    return problems


def visibility_test_problems(sites: list[dict], tree_root: Path,
                             tree_kind: str = "export") -> list[str]:
    """Registry `visibility_test` node ids ("<path>::<test name>") that do not exist in
    `tree_root`. Only entries whose site applies to this kind of tree are checked: the
    test for an export-only site need not exist in the public repo."""
    problems: list[str] = []
    shipped: set[str] | None = None
    for site in sites:
        node = site.get("visibility_test")
        if not node or not _applies(site, tree_kind):
            continue
        key = (site.get("file", "?"), site.get("symbol", "?"))
        rel, _, name = str(node).partition("::")
        name = name.split("[", 1)[0]
        path = tree_root / rel
        if shipped is None:
            shipped = shipped_files(tree_root).files
        if not rel or not name or PurePosixPath(rel).as_posix() not in shipped:
            problems.append(f"{key}: visibility_test {node!r} -- no such shipped file in this "
                            f"tree")
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
    shipped = shipped_files(shipped_tree)
    findings = scan_tree(shipped_tree, shipped)
    kind = tree_kind(shipped_tree)
    sites = load_registry(shipped_tree)
    problems = (shipped.problems + registry_problems(findings, sites, kind)
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
    """A literal (or literal-bound) name is an import; any other name fails closed."""
    src = ("import importlib\nfrom importlib import import_module as im\n"
           "importlib.import_module('pkg.withheld')\nim('pkg.sibling')\n"
           "__import__('json')\nimportlib.import_module(name_from_config)\n"
           "for _, mod in (('a', 'pkg.sibling'), ('b', 'json')):\n    im(mod)\n")
    found = scan_source(src, "bin/x.py", fake_tree)
    assert _kinds(found) == {("import", "pkg.withheld"), ("path", "importlib.import_module")}
    assert [f.why for f in found if f.kind == "path"] == ["unresolved"]


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
