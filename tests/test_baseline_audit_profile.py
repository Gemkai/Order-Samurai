"""A clean Claude Code install must produce zero FAILs.

Measured 2026-07-31, before the audit-profile split: pointing the verify_claude_*
pack at a fresh install produced **22 FAILs, all of them "required thing missing"
and none of them a defect**. The requiredDirectories/requiredFiles lists were a
portrait of the machine the pack was written on (hooks/, orchestration/, safety/,
skills-lock.json, subagent-lock.json, commands/doctor.md, ...).

A first run that is 22-for-22 wrong is how an auditor loses its user, so this is
the pack's headline product requirement, not a nicety. This test is the fixture
that keeps it true: any new rule that assumes the authoring machine's layout will
fail here rather than in a stranger's terminal.

The strict tier is still asserted — see test_full_profile_still_asserts_layout.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

OS_ROOT = Path(__file__).resolve().parents[1]
VERIFIERS = sorted((OS_ROOT / "execution").glob("verify_claude_*.py"))


@pytest.fixture
def fresh_install(tmp_path: Path) -> Path:
    """What a real Claude Code install has before any customisation.

    settings.json is the one artifact the harness itself guarantees; every
    directory below is created on demand, and a brand-new install may have none
    of them. Deliberately minimal -- padding this fixture out to resemble a
    mature control plane would defeat its purpose.
    """
    root = tmp_path / "fresh-claude"
    root.mkdir()
    (root / "settings.json").write_text('{\n  "model": "opus"\n}\n', encoding="utf-8")
    for d in ("commands", "skills", "projects"):
        (root / d).mkdir()
    return root


def _run(verifier: Path, root: Path, profile: str | None) -> tuple[int, str]:
    env = {**os.environ, "CLAUDE_RUNTIME_ROOT": str(root)}
    if profile is None:
        env.pop("ORDER_SAMURAI_AUDIT_PROFILE", None)
    else:
        env["ORDER_SAMURAI_AUDIT_PROFILE"] = profile
    proc = subprocess.run(
        [sys.executable, str(verifier)],
        capture_output=True, text=True, timeout=120, cwd=str(OS_ROOT), env=env,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _fails(output: str) -> list[str]:
    return [ln for ln in output.splitlines() if ln.startswith("[FAIL]")]


def test_verifier_pack_is_not_empty() -> None:
    """Guard the guard: a glob that silently matched nothing would pass everything."""
    assert len(VERIFIERS) >= 12, f"expected the claude_* pack, found {VERIFIERS}"


def test_clean_install_reports_zero_failures(fresh_install: Path) -> None:
    """The default (baseline) profile must not fail a stock install."""
    offenders: dict[str, list[str]] = {}
    for verifier in VERIFIERS:
        _, out = _run(verifier, fresh_install, profile=None)
        fails = _fails(out)
        if fails:
            offenders[verifier.name] = fails
    assert not offenders, (
        "a clean Claude Code install must produce zero FAILs on the baseline "
        f"profile; got: {offenders}"
    )


def test_every_verifier_reaches_a_summary(fresh_install: Path) -> None:
    """A verifier that crashes on an unfamiliar target is worse than a noisy one."""
    missing = [
        v.name for v in VERIFIERS
        if "Summary:" not in _run(v, fresh_install, profile=None)[1]
    ]
    assert not missing, f"no Summary line (crashed?) on a clean install: {missing}"


def test_full_profile_still_asserts_layout(fresh_install: Path) -> None:
    """The strict tier must survive. If this passes, baseline swallowed everything."""
    total = sum(len(_fails(_run(v, fresh_install, profile="full")[1])) for v in VERIFIERS)
    assert total > 0, (
        "the full profile asserted nothing a clean install lacks -- the "
        "opinionated tier has been hollowed out"
    )


def test_unknown_profile_is_rejected_not_downgraded(fresh_install: Path) -> None:
    """A typo must not silently become 'baseline' and disable the strict tier."""
    code, out = _run(VERIFIERS[0], fresh_install, profile="strictt")
    assert code != 0, "an unrecognised profile exited cleanly"
    assert "ORDER_SAMURAI_AUDIT_PROFILE" in out, out[-400:]


def test_active_profile_is_reported(fresh_install: Path) -> None:
    """A weakened run must be visible in its own output, not inferred."""
    _, out = _run(OS_ROOT / "execution" / "verify_claude_root_hygiene.py",
                  fresh_install, profile=None)
    assert "Profile: baseline" in out, out[:400]
