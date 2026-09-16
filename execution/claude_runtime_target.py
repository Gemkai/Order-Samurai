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
_HARDENING_REPORT_REL = "reports/2026-04-12-claude-architecture-hardening-report.md"
REPORT_PATH = ROOT_DIR / _HARDENING_REPORT_REL
BACKLOG_PATH = ROOT_DIR / "backlog" / "claude_verifier_backlog.md"

#: Repo-relative pack artifacts ``bin/extract_public.py`` never ships (its
#: "Section 2 — never ship" list). ONE declaration, read two ways: absent here in a
#: nested Agentica checkout is pack rot and must FAIL; absent in a standalone
#: distribution is the export's own policy and must not be reported as a defect.
#: Scattering the filename across each check is how those two readings drift apart.
#: The backlog is deliberately NOT in this set — it ships, so its absence is rot in
#: both layouts.
INTERNAL_ONLY_ARTIFACTS: tuple[str, ...] = (_HARDENING_REPORT_REL,)

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


def governance_root(start: Path | None = None) -> Path:
    """The directory that holds ``agentica_core/`` and ``config/`` for this pack.

    ``<repo>/Governance`` inside the Agentica repo; the pack root itself in the
    public export, where ``bin/extract_public.py`` flattens the pack and lays the
    allow-listed Governance files (``agentica_core/``, ``schema/``,
    ``bin/sensei_writeback.py``, ``tools/operator_registry_check.py``) beside it.

    Anything that reaches a Governance-level file through a fixed hop
    (``_ROOT.parent / "config"``, ``parents[3] / "Governance" / "tools"``)
    resolves OUTSIDE the export -- the exact defect class the export gate exists
    for. 2026-09-06: hitl_alerts' operator registry and scheduled_run_outcomes'
    registry loader both did, 21 exported-suite failures between them, and the
    gate had never once been green. Resolve through this instead.

    ``start`` exists for the same reason it does on ``agentica_repo_root``: the
    layout logic is testable against synthetic trees.
    """
    repo = agentica_repo_root(start)
    if repo is not None:
        return repo / "Governance"
    origin = (start or Path(__file__)).resolve()
    return origin.parents[1]


def runtime_root() -> Path:
    """The live Claude home. CLAUDE_RUNTIME_ROOT overrides for tests/sandboxes
    (and would-be other hosts); default is this machine's ~/.claude."""
    override = os.environ.get("CLAUDE_RUNTIME_ROOT")
    return Path(override).expanduser() if override else Path.home() / ".claude"


#: Audit profiles. "baseline" asserts only what EVERY Claude Code install has;
#: "full" additionally asserts this control plane's opinionated layout.
BASELINE_PROFILE = "baseline"
FULL_PROFILE = "full"
_PROFILES = (BASELINE_PROFILE, FULL_PROFILE)

#: Markers of a MATURE control plane at a Claude home -- the same opinionated
#: layout the requiredDirectories/requiredFiles lists assert. Mirrors the repo
#: surface's own layout-derived tier (commit 7d35e936: standalone vs nested
#: Agentica checkout), applied here to ~/.claude's own contents instead --
#: "nested in an Agentica checkout" says nothing about the OPERATOR's Claude
#: home, so that signal can't be reused; the home's own directory listing can.
_MATURE_CLAUDE_HOME_MARKERS: tuple[str, ...] = (
    "hooks",
    "orchestration",
    "safety",
    "skills-lock.json",
    "subagent-lock.json",
)


def claude_home_has_mature_layout(home: Path | None = None) -> bool:
    """True when `home` (default: `runtime_root()`) shows this control plane's
    opinionated layout -- every marker in `_MATURE_CLAUDE_HOME_MARKERS` present.

    Read-only existence check, no side effects. Used only to pick the honest
    default for `audit_profile()`; ORDER_SAMURAI_AUDIT_PROFILE still overrides
    it explicitly either way.
    """
    base = home if home is not None else runtime_root()
    return all((base / marker).exists() for marker in _MATURE_CLAUDE_HOME_MARKERS)


def audit_profile(default: str | None = None) -> str:
    """Which tier of requirements to assert. ORDER_SAMURAI_AUDIT_PROFILE selects.

    `default` is what an UNSET variable means, and exists because the honest
    default differs per surface. A verifier whose target IS this pack can infer
    its tier from "am I a nested Agentica checkout" (see verify_root_hygiene) and
    passes an explicit `default`. For ~/.claude, nothing here says whether the
    OPERATOR's Claude home has the opinionated layout -- being nested in Agentica
    doesn't imply it -- so when `default` is left unset (None) it is derived
    instead from `claude_home_has_mature_layout()`: "full" only when ~/.claude
    itself already shows the mature markers, "baseline" otherwise. Keeping one
    env-parsing implementation keeps the dial's spelling and its typo-rejection
    identical on every surface.

    The requiredDirectories/requiredFiles lists describe a MATURE control plane
    (hooks/, orchestration/, safety/, skills-lock.json, subagent-lock.json, ...).
    Measured 2026-07-31: asserting them against a clean Claude Code install
    produces 22 FAILs, all of which are "required thing missing" and none of
    which is a defect -- the policy was a portrait of the machine it was written
    on. A first run that is 22/22 wrong is how an auditor loses its user, so an
    immature ~/.claude still gets the conservative "baseline" default -- only a
    ~/.claude that already has the layout gets "full" without an explicit env var.

    Set ORDER_SAMURAI_AUDIT_PROFILE=full (or =baseline) to override the derived
    default explicitly on any host. Verifiers print the active profile so a
    weakened -- or silently un-strengthened -- run is never silent.

    An unrecognised value raises rather than silently downgrading: a typo'd
    profile that quietly became "baseline" would disable the strict tier without
    anyone noticing, which is the failure mode this whole contract guards.
    """
    if default is None:
        default = FULL_PROFILE if claude_home_has_mature_layout() else BASELINE_PROFILE
    if default not in _PROFILES:
        raise ValueError(f"default={default!r} is not one of {_PROFILES}")
    raw = (os.environ.get("ORDER_SAMURAI_AUDIT_PROFILE") or default).strip().lower()
    if raw not in _PROFILES:
        raise ValueError(
            f"ORDER_SAMURAI_AUDIT_PROFILE={raw!r} is not one of {_PROFILES}"
        )
    return raw


def required_sections(profile: str | None = None) -> tuple[str, str]:
    """(directories_key, files_key) in a hygiene policy for `profile`.

    Defaults to the ~/.claude surface's active profile; pass one explicitly when
    the caller has resolved it for a different target.
    """
    if (profile or audit_profile()) == FULL_PROFILE:
        return ("requiredDirectories", "requiredFiles")
    return ("baselineRequiredDirectories", "baselineRequiredFiles")


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
