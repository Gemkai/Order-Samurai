"""Per-platform verifier providers. The coupling to each platform's real verifiers
lives here, named from platforms.json, so the kernel itself stays platform-neutral.

Both Order Samurai (Claude) and Jarvis/Core (Antigravity) ship a top-level `execution`
package. Python caches modules by name, so importing both in one process would collide.
`load_run_checks` imports each platform's `execution` package in an isolated namespace
so multiple platforms coexist (tests, or a future "check every platform" doctor).
"""
from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path


def _execution_module_keys() -> list[str]:
    return [k for k in sys.modules if k == "execution" or k.startswith("execution.")]


@contextmanager
def _isolated_execution(repo_root: Path):
    saved_path = list(sys.path)
    saved_modules = {k: sys.modules[k] for k in _execution_module_keys()}
    for k in saved_modules:
        del sys.modules[k]
    sys.path.insert(0, str(repo_root))
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for k in _execution_module_keys():  # drop the ones we just imported
            del sys.modules[k]
        sys.modules.update(saved_modules)   # restore any previously-cached platform


def load_run_checks(repo_root: Path, imports: list[tuple[str, str]]) -> list:
    """Import (module, attr) pairs from a platform repo's `execution` package, isolated.

    The returned callables keep working after the context exits — they hold their own
    module globals (paths captured at import), and no platform verifier imports at call time.
    """
    if not repo_root.exists():
        raise RuntimeError(f"platform repo not found at {repo_root}")
    fns = []
    with _isolated_execution(repo_root):
        for module_name, attr in imports:
            fns.append(getattr(importlib.import_module(module_name), attr))
    return fns
