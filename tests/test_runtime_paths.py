"""execution/runtime_paths.governance_root — the two-layout resolver.

The pack ships in two shapes: nested (`<repo>/Governance/Order Samurai/`) and flat
(the tree `bin/extract_public.py` builds, where the pack IS the root). Anything that
spells the Governance root as a fixed number of hops is correct in one and silently
wrong in the other. Measured in a real export build: doctor's audit canary passed
`GOVERNANCE_ROOT=/private/tmp` — the temp dir ABOVE the export — so the audit gate
could not import `agentica_core` and the canary reported the gate broken.

Synthetic trees rather than the live repo: a resolver that only works on the machine
that wrote it is the bug under test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution.runtime_paths import governance_root  # noqa: E402


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch):
    """The live process may carry a GOVERNANCE_ROOT; these assert the walk."""
    monkeypatch.delenv("GOVERNANCE_ROOT", raising=False)


def _tree(root: Path, pack_rel: str, marker_rel: str) -> Path:
    """Build a synthetic layout; return the pack's execution/ dir (the caller)."""
    pack = root / pack_rel
    (pack / "execution").mkdir(parents=True)
    (root / marker_rel / "agentica_core").mkdir(parents=True)
    return pack / "execution"


def test_nested_layout_resolves_to_the_governance_dir(tmp_path):
    caller = _tree(tmp_path, "Governance/Order Samurai", "Governance")

    assert governance_root(caller / "doctor.py") == tmp_path / "Governance"


def test_flat_export_layout_resolves_to_the_pack_root(tmp_path):
    # The regression: the pack root itself holds agentica_core/, so the answer is
    # the pack -- not tmp_path, which is the export's parent and holds nothing.
    caller = _tree(tmp_path, "order-samurai", "order-samurai")

    assert governance_root(caller / "doctor.py") == tmp_path / "order-samurai"


def test_explicit_override_wins_over_the_walk(tmp_path, monkeypatch):
    """The reflex engine exports GOVERNANCE_ROOT when it spawns these scripts."""
    caller = _tree(tmp_path, "Governance/Order Samurai", "Governance")
    monkeypatch.setenv("GOVERNANCE_ROOT", str(tmp_path / "elsewhere"))

    assert governance_root(caller / "doctor.py") == tmp_path / "elsewhere"


def test_the_live_tree_resolves_to_a_dir_that_actually_holds_agentica_core():
    """Whichever layout this suite is running in, the answer must be usable —
    an importable agentica_core is the whole reason the path is computed."""
    assert (governance_root() / "agentica_core").is_dir()
