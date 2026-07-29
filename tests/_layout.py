"""Locate the Governance root without assuming how deep the tree is.

Tests historically wrote `Path(__file__).resolve().parents[2]` to mean "the
Governance directory". That is true in the live repo:

    AgenticaOS/Governance/Order Samurai/tests/test_x.py
                ^parents[2]

and false in the public product, which is FLAT — `extract_public.py` exports the
`Governance/Order Samurai` subtree to the repo root, so the same expression walks
straight out of the repository:

    Order Samurai(product)/tests/test_x.py
    ^^^^^^^^^^^^^^^^^^^^^^ parents[1]      parents[2] == ~/Desktop/Solutions

The consequence was invisible for a long time because the affected tests could not
even be collected in the public tree (their `agentica_core` imports failed first).
Fixing the imports turned 63 collection errors into 63 assertion failures — the
path bug had simply been masked by an earlier one.

Resolve by MARKER, not by depth: walk up until a directory contains
`agentica_core/`. That is `Governance/` in the nested layout and the repo root in
the flat one, which is exactly the directory both layouts use as the anchor for
`schema/`, `api/` and `bin/`.
"""

from __future__ import annotations

from pathlib import Path

_MARKER = "agentica_core"


def governance_root(start: str | Path) -> Path:
    """Nearest ancestor of `start` containing `agentica_core/`.

    Pass `__file__`. Returns `Governance/` in the live tree and the repo root in
    the exported product tree.

    Raises rather than guessing: a silent wrong root sends tests looking for
    schema files outside the repository, which is the failure this module exists
    to end. A loud error at import time is cheaper than 63 confusing assertions.
    """
    here = Path(start).resolve()
    for candidate in here.parents:
        if (candidate / _MARKER).is_dir():
            return candidate
    raise RuntimeError(
        f"no ancestor of {here} contains {_MARKER}/ — cannot locate the "
        f"Governance root. In the live repo that is Governance/; in the exported "
        f"product tree it is the repo root."
    )


def governance_bin(start: str | Path) -> Path:
    """`bin/` beside the Governance root — where the sensei helper modules live."""
    return governance_root(start) / "bin"
