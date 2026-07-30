"""Pins the canonical location of the meditation halt flag: state/MEDITATION_STOP.

2026-07-30: the flag had THREE different paths across its consumers, and the two
that mattered pointed at a file that has never existed:

  bin/ronin:17                 STOP_FILE="$REPO_DIR/MEDITATION_STOP"
  bin/meditation_overnight.sh  [ -f "$MAIN_DIR/MEDITATION_STOP" ]
  bin/ronin-daemon.sh:249      [ -f MEDITATION_STOP ]        # cwd == $REPO_DIR

The live flag is state/MEDITATION_STOP — the overnight prompt's STEP A reads a
bare `MEDITATION_STOP`, which the runner's injected PATH_HEADER re-roots to
$STATE_DIR in the main tree, and .gitignore covers exactly that path. So cycles
really did halt on state/MEDITATION_STOP while `bin/ronin status` reported "no
MEDITATION_STOP" and `bin/ronin arm` rm'd a nonexistent repo-root path — meaning
the documented way to clear the halt could not clear it.

Two layers here, because the two heavy consumers cannot be cheaply executed:
  1. behavioral — actually run `bin/ronin status` against a temp repo and prove
     it reads state/ and does NOT read the repo root.
  2. static — assert no consumer references a non-state MEDITATION_STOP path.

The static layer is what stops silent re-divergence in the scripts we can't run.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
RONIN = BIN / "ronin"
DAEMON = BIN / "ronin-daemon.sh"
OVERNIGHT = BIN / "meditation_overnight.sh"

CANONICAL_RELATIVE = "state/MEDITATION_STOP"

# Every consumer that gates on the flag. Each must resolve it under its own
# STATE_DIR, exactly like it already does for budget_ledger.json /
# MEDITATION_STATE.json — MEDITATION_STOP was the lone state file that escaped
# that convention, which is how it drifted.
CONSUMERS = [RONIN, DAEMON, OVERNIGHT]


def _strip_comments(text: str) -> list[str]:
    """Executable lines only — prose in header comments is not a wiring bug."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


# A directory-qualified use: something ending in "/" immediately before the name.
# Captures the qualifier so we can check it is a state dir. This deliberately does
# NOT match `log "MEDITATION_STOP present"` — a human-readable message that happens
# to contain the name is not a wiring bug.
_QUALIFIED = re.compile(r'([\w$\{\}./-]*/)MEDITATION_STOP')
# A bare use in a command position: file test, rm, touch, or assignment. This is
# the cwd-relative form, which is a path reference even with no "/" in it.
_BARE_PATH_USE = re.compile(r'(-[fe]\s+|\brm\s+(?:-\w+\s+)*|\btouch\s+|=)"?MEDITATION_STOP\b')
_STATE_ROOTED = re.compile(r'(\$\{?STATE_DIR\}?|(^|/)state)/?$')


@pytest.mark.parametrize("script", CONSUMERS, ids=lambda p: p.name)
def test_consumer_resolves_stop_file_under_state_dir(script: Path) -> None:
    """Every path reference to the flag must resolve under that script's STATE_DIR."""
    assert script.exists(), f"missing consumer {script}"
    offenders = []
    for line in _strip_comments(script.read_text(encoding="utf-8")):
        if "MEDITATION_STOP" not in line:
            continue
        for qualifier in _QUALIFIED.findall(line):
            if not _STATE_ROOTED.search(qualifier.rstrip("/") + "/"):
                offenders.append(f"{line.strip()}   [qualifier: {qualifier}]")
        if _BARE_PATH_USE.search(line):
            offenders.append(f"{line.strip()}   [bare, cwd-relative]")
    assert not offenders, (
        f"{script.name} references MEDITATION_STOP outside state/:\n  "
        + "\n  ".join(offenders)
        + f"\nThe canonical path is {CANONICAL_RELATIVE} (see this test's docstring)."
    )


def test_at_least_one_real_path_reference_survives_the_filter() -> None:
    """Guards the guard: if the regexes stopped matching anything, the test above
    would pass vacuously no matter how badly the paths drifted."""
    seen = {}
    for script in CONSUMERS:
        count = 0
        for line in _strip_comments(script.read_text(encoding="utf-8")):
            count += len(_QUALIFIED.findall(line)) + len(_BARE_PATH_USE.findall(line))
        seen[script.name] = count
    assert all(c > 0 for c in seen.values()), (
        f"no MEDITATION_STOP path reference detected in some consumer: {seen}. "
        "Either a consumer stopped gating on the flag, or the detection regexes rotted."
    )


def _make_repo(tmp_path: Path) -> Path:
    """Minimal Order Samurai skeleton that `bin/ronin status` can run against."""
    repo = tmp_path / "Order Samurai"
    (repo / "bin").mkdir(parents=True)
    (repo / "state").mkdir()
    (repo / "artifacts").mkdir()
    for src in (RONIN, DAEMON):
        dst = repo / "bin" / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        dst.chmod(0o755)
    return repo


def _status(repo: Path) -> str:
    proc = subprocess.run(
        ["bash", str(repo / "bin" / "ronin"), "status"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "MEDITATION_DRYRUN": "1"},
    )
    return proc.stdout + proc.stderr


def test_status_reports_present_when_flag_is_in_state(tmp_path: Path) -> None:
    """The bug: this reported 'no MEDITATION_STOP' while cycles halted on it."""
    repo = _make_repo(tmp_path)
    (repo / "state" / "MEDITATION_STOP").write_text("halt\n", encoding="utf-8")
    out = _status(repo)
    assert "MEDITATION_STOP present" in out, (
        "bin/ronin status did not see state/MEDITATION_STOP — the real flag.\n" + out
    )


def test_status_does_not_honor_a_repo_root_flag(tmp_path: Path) -> None:
    """Pins the direction of the fix: repo-root is NOT a second valid location.

    Without this, someone could 'fix' a future drift by making consumers accept
    both paths, which reintroduces the ambiguity this test exists to prevent.
    """
    repo = _make_repo(tmp_path)
    (repo / "MEDITATION_STOP").write_text("halt\n", encoding="utf-8")
    out = _status(repo)
    assert "no MEDITATION_STOP" in out, (
        "bin/ronin status honored a repo-root MEDITATION_STOP; state/ is canonical.\n" + out
    )
