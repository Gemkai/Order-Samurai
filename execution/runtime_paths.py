from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def governance_root(start: Path | None = None) -> Path:
    """The directory holding ``agentica_core/`` — valid in BOTH layouts.

    ``Governance/`` in the nested live repo, and the pack root itself in the flat
    tree ``bin/extract_public.py`` builds. Spelling it as ``REPO_ROOT.parent`` is
    only correct nested; in the export that hop lands in the export's PARENT
    directory (``/tmp`` for a scratch build), so anything handed that path as
    GOVERNANCE_ROOT cannot import ``agentica_core`` and fails in a way that reads
    like the subject being broken. Same bug class as ``tests/_layout.py``.

    An explicit GOVERNANCE_ROOT wins: the reflex engine already exports one when
    it spawns these scripts, and emulating the engine means honouring it.
    """
    override = os.environ.get("GOVERNANCE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "agentica_core").is_dir():
            return candidate
    return REPO_ROOT.parent  # historical nested-layout fallback
BACKLOG_DIR = REPO_ROOT / "backlog"
CONFIG_DIR = REPO_ROOT / "config"
EXECUTION_DIR = REPO_ROOT / "execution"
REPORTS_DIR = REPO_ROOT / "reports"
TESTS_DIR = REPO_ROOT / "tests"
TMP_DIR = REPO_ROOT / ".tmp"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

ROOT_HYGIENE_POLICY_PATH = CONFIG_DIR / "root_hygiene_policy.json"
PROMOTION_POLICY_PATH = CONFIG_DIR / "promotion_policy.json"
ANTI_SPRAWL_POLICY_PATH = CONFIG_DIR / "anti_sprawl_policy.json"
ANTI_DRIFT_POLICY_PATH = CONFIG_DIR / "anti_drift_policy.json"
STATE_DIR = REPO_ROOT / "state"
SCOUTS_DIR = REPO_ROOT / "scouts"
