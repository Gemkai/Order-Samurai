"""Canonical target contract for the Claude runtime enforcement pack.

Backlog item 1 (claude_verifier_backlog.md): every verify_claude_* verifier
imports its target paths from here instead of re-declaring them. No side
effects on import.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
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


def agentica_repo_root(start: Path | None = None) -> Path | None:
    """The Agentica repo root above `start` (default: this module), by MARKER.

    The pack lives at ``<repo>/Governance/Order Samurai/``, so the root is the
    parent of the ``Governance`` dir above it. Encoding that as a fixed
    ``parents[N]`` breaks in the public export, where the pack is flattened to
    the root and the same hop count lands OUTSIDE the distribution — verifiers
    then measured whatever directory happened to be there.

    Returns None when there is no Agentica repo above this tree. `start` exists
    so the marker logic is testable against synthetic layouts: a silent None in
    the live repo would turn root-hygiene checks into a passing no-op, which is
    the failure mode this whole resolver was written to avoid.
    """
    origin = (start or Path(__file__)).resolve()
    for ancestor in origin.parents:
        if ancestor.name == "Governance" and (ancestor / "agentica_core").is_dir():
            return ancestor.parent
    return None


def is_standalone_distribution() -> bool:
    """True when this pack ships without the Agentica repo around it.

    Internal-only artifacts are absent there BY DESIGN — ``bin/extract_public.py``
    never ships ``reports/2026-04-12-*`` (its "Section 2 — never ship" list), so a
    verifier that treats their absence as pack rot is reporting the export's own
    policy back as a defect. Absent-by-design and absent-by-rot are different
    findings and must not share a status.
    """
    return agentica_repo_root() is None


def runtime_root() -> Path:
    """The live Claude home. CLAUDE_RUNTIME_ROOT overrides for tests/sandboxes
    (and would-be other hosts); default is this machine's ~/.claude."""
    override = os.environ.get("CLAUDE_RUNTIME_ROOT")
    return Path(override).expanduser() if override else Path.home() / ".claude"


# ---------------------------------------------------------------------------
# Absolute home-rooted runtime paths — the shared DENYLIST matcher.
#
# Each verify_claude_* verifier used to carry its own tuple of literal paths,
# one of which was this machine's own home. The public exporter scrubs
# "/Users/<owner>" -> "~" and "C:\Users\<owner>\.claude" -> "~/.claude", which
# rewrote a DENYLIST entry into "~/.claude" — the portable form these verifiers
# exist to ACCEPT. Every check inverted, in the exported tree only, where no one
# was watching: a portable command was reported as pinned and a genuinely pinned
# one slipped through.
#
# A pattern carries no identifier, so the scrubber has nothing to rewrite and
# the live and exported trees agree by construction. It is also strictly
# stronger than the literals it replaces — a fixed literal only ever caught THIS
# machine's home, so another user's pinned path was invisible to it.
# ---------------------------------------------------------------------------
#: An optional leading mount segment, so a home reached through a mount point
#: ("/Volumes/Users/<user>/.claude" — the dead SMB path this repo migrated off)
#: is reported in full rather than as a truncated "/Users/..." substring.
_MOUNT = r"(?:/[^/\s\"']+)?"
_HOME_PREFIX = r"(?:[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}|" + _MOUNT + r"/Users/|/home/)"
_USER_SEGMENT = r"[^\\/\s\"']+"
#: Matches "\", "/", and the doubled "\\" that JSON and Python source encoding produce.
_SEP = r"[\\/]{1,2}"


@lru_cache(maxsize=None)
def home_rooted_re(runtime_dir: str) -> re.Pattern[str]:
    """Regex matching any absolute home-rooted path to ``runtime_dir``.

    ``runtime_dir`` is written POSIX-style (``.claude``, ``.gemini/antigravity``);
    every separator in it matches ``/``, ``\\`` or the doubled ``\\\\``.
    """
    tail = _SEP.join(re.escape(part) for part in runtime_dir.split("/"))
    return re.compile(_HOME_PREFIX + _USER_SEGMENT + _SEP + tail)


def canonical_home_path(match: str) -> str:
    """Collapse a doubled-backslash match to its single-backslash spelling.

    Offender strings are reported to humans and asserted in tests, so one
    encoding of a path must not read as two different findings.
    """
    return match.replace("\\\\", "\\")


def pinned_home_paths(text: str, *runtime_dirs: str) -> list[str]:
    """Every distinct absolute home-rooted runtime path in ``text``, canonicalised."""
    hits = {
        canonical_home_path(m.group(0))
        for runtime_dir in runtime_dirs
        for m in home_rooted_re(runtime_dir).finditer(text)
    }
    return sorted(hits)
