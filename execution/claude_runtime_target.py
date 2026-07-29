"""Canonical target contract for the Claude runtime enforcement pack.

Backlog item 1 (claude_verifier_backlog.md): every verify_claude_* verifier
imports its target paths from here instead of re-declaring them. No side
effects on import.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT_DIR / "config"

SCORECARD_PATH = CONFIG_DIR / "claude_architecture_scorecard.json"
ANTI_DRIFT_POLICY_PATH = CONFIG_DIR / "claude_anti_drift_policy.json"
ANTI_SPRAWL_POLICY_PATH = CONFIG_DIR / "claude_anti_sprawl_policy.json"
ROOT_HYGIENE_POLICY_PATH = CONFIG_DIR / "claude_root_hygiene_policy.json"
PROMOTION_POLICY_PATH = CONFIG_DIR / "claude_promotion_policy.json"
SURFACE_MATRIX_PATH = CONFIG_DIR / "claude_surface_matrix.json"
REPORT_PATH = ROOT_DIR / "reports" / "2026-04-12-claude-architecture-hardening-report.md"
BACKLOG_PATH = ROOT_DIR / "backlog" / "claude_verifier_backlog.md"

ALL_POLICY_PATHS = (
    SCORECARD_PATH,
    ANTI_DRIFT_POLICY_PATH,
    ANTI_SPRAWL_POLICY_PATH,
    ROOT_HYGIENE_POLICY_PATH,
    PROMOTION_POLICY_PATH,
    SURFACE_MATRIX_PATH,
)


def runtime_root() -> Path:
    """The live Claude home. CLAUDE_RUNTIME_ROOT overrides for tests/sandboxes
    (and would-be other hosts); default is this machine's ~/.claude."""
    override = os.environ.get("CLAUDE_RUNTIME_ROOT")
    return Path(override).expanduser() if override else Path.home() / ".claude"
