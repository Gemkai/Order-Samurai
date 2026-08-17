"""`ronin promote` must deliver into the queue that actually runs.

2026-08-08: promote moved ratified PROPOSED_BACKLOG items into
`state/MEDITATION_STATE.json` — the backlog of the meditation loop the owner
PAUSED and launchctl-disabled on 2026-07-30, with the explicit decision that
backlog items land "in reviewed sessions, NOT unattended overnight Opus cycles".
So promote had never delivered a single item: MEDITATION_STATE.json still held
only its 9 seed items while 56 proposals sat behind a command whose output went
nowhere. A producer whose consumer does not run.

The fix (user-ratified: "Repoint at the /goal sweep") is a STATUS TRANSITION
in place, not a copy into a fourth queue file: the nightly `/goal` sweep already
reads `Governance/Order Samurai/state/PROPOSED_BACKLOG.json` at an absolute path
and works the items whose status marks them approved for work. Promotion sets
that status; the item never leaves the file the sweep reads.

These tests pin three things:
  1. behavioral — run `bin/ronin promote` against a temp repo and prove the
     status flips in PROPOSED_BACKLOG.json and MEDITATION_STATE.json is byte-
     identical afterwards.
  2. the human gate — an item without `approved: true` is never promoted.
  3. static — no line of the promote path writes MEDITATION_STATE.json, so the
     old destination cannot creep back in.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
RONIN = BIN / "ronin"
DAEMON = BIN / "ronin-daemon.sh"

# The status a promoted item carries. This string is the contract between
# `ronin promote` and the /goal sweep's Phase 1 item 3 ("items whose status marks
# them approved/triaged for work") — changing it here without changing the sweep
# silently re-orphans the queue.
PROMOTED_STATUS = "approved_for_work"

# What the sweep opens, verbatim from ~/.claude/skills/goal/SKILL.md Phase 1.3.
SWEEP_SOURCE_RELATIVE = "state/PROPOSED_BACKLOG.json"

_SEED_STATE = {
    "cycle": 0,
    "run_id": "SEED",
    "backlog": [{"id": "BOW-001", "title": "Seed item", "status": "todo"}],
}


def _make_repo(tmp_path: Path, items: list[dict]) -> Path:
    """Minimal Order Samurai skeleton `bin/ronin promote` can run against."""
    repo = tmp_path / "Order Samurai"
    (repo / "bin").mkdir(parents=True)
    (repo / "state").mkdir()
    (repo / "artifacts").mkdir()
    for src in (RONIN, DAEMON):
        dst = repo / "bin" / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        dst.chmod(0o755)
    (repo / SWEEP_SOURCE_RELATIVE).write_text(
        json.dumps({"generated_at": "2026-08-08", "items": items}, indent=2),
        encoding="utf-8",
    )
    (repo / "state" / "MEDITATION_STATE.json").write_text(
        json.dumps(_SEED_STATE, indent=2), encoding="utf-8"
    )
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "bin" / "ronin"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "MEDITATION_DRYRUN": "1"},
    )


def _proposed(repo: Path) -> list[dict]:
    return json.loads(
        (repo / SWEEP_SOURCE_RELATIVE).read_text(encoding="utf-8")
    )["items"]


def test_promote_marks_the_item_in_the_file_the_sweep_reads(tmp_path: Path) -> None:
    """The item must be discoverable by the sweep: still in PROPOSED_BACKLOG.json,
    carrying the status that marks it approved for work."""
    repo = _make_repo(
        tmp_path,
        [{"id": "AUTO-100", "pillar": "bow", "title": "Wire X", "status": "proposed",
          "approved": True}],
    )
    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    items = _proposed(repo)
    assert len(items) == 1, f"promote removed the item from the sweep's source: {items}"
    item = items[0]
    assert item["status"] == PROMOTED_STATUS, item
    assert item["approved"] is True, "the human-ratification record must survive promotion"
    assert item.get("promoted_at"), "promotion must be timestamped"
    assert "AUTO-100" in proc.stdout and PROMOTED_STATUS in proc.stdout


def test_promote_does_not_touch_meditation_state(tmp_path: Path) -> None:
    """The paused loop's backlog is no longer promote's destination."""
    repo = _make_repo(
        tmp_path,
        [{"id": "AUTO-101", "pillar": "sword", "title": "Wire Y", "status": "proposed",
          "approved": True}],
    )
    state_path = repo / "state" / "MEDITATION_STATE.json"
    before = state_path.read_bytes()

    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert state_path.read_bytes() == before, (
        "promote wrote MEDITATION_STATE.json — that backlog belongs to the paused "
        "meditation loop and is not a delivery channel."
    )


def test_promote_runs_without_meditation_state_present(tmp_path: Path) -> None:
    """promote used to hard-error when MEDITATION_STATE.json was missing. It no
    longer reads that file, so its absence must not block delivery."""
    repo = _make_repo(
        tmp_path,
        [{"id": "AUTO-102", "pillar": "brush", "title": "Wire Z", "status": "proposed",
          "approved": True}],
    )
    (repo / "state" / "MEDITATION_STATE.json").unlink()

    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _proposed(repo)[0]["status"] == PROMOTED_STATUS


def test_unapproved_items_are_never_promoted(tmp_path: Path) -> None:
    """The human gate: only an operator setting approved:true and running promote
    can move an item into the sweep's work set."""
    repo = _make_repo(
        tmp_path,
        [
            {"id": "AUTO-200", "title": "Not ratified", "status": "proposed",
             "approved": False},
            {"id": "AUTO-201", "title": "Ratified", "status": "proposed",
             "approved": True},
        ],
    )
    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    by_id = {i["id"]: i for i in _proposed(repo)}
    assert by_id["AUTO-200"]["status"] == "proposed"
    assert "promoted_at" not in by_id["AUTO-200"]
    assert by_id["AUTO-201"]["status"] == PROMOTED_STATUS


def test_promote_is_idempotent(tmp_path: Path) -> None:
    """Re-running promote must not re-stamp an item already awaiting the sweep."""
    repo = _make_repo(
        tmp_path,
        [{"id": "AUTO-300", "title": "Wire W", "status": "proposed", "approved": True}],
    )
    assert _run(repo, "promote").returncode == 0
    first_stamp = _proposed(repo)[0]["promoted_at"]

    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No new approved items" in proc.stdout, proc.stdout
    assert _proposed(repo)[0]["promoted_at"] == first_stamp


def test_status_reports_the_promoted_count(tmp_path: Path) -> None:
    """`ronin status` is the operator's view of the queue; it must name the real
    destination rather than implying the items went to the meditation backlog."""
    repo = _make_repo(
        tmp_path,
        [
            {"id": "AUTO-400", "title": "A", "status": PROMOTED_STATUS, "approved": True},
            {"id": "AUTO-401", "title": "B", "status": "proposed", "approved": True},
            {"id": "AUTO-402", "title": "C", "status": "proposed", "approved": False},
        ],
    )
    out = _run(repo, "status").stdout
    assert "Proposed: 3 items (1 approved, 1 promoted -> /goal sweep)" in out, out


# A PATH use of the meditation state file: the `$STATE_FILE` variable, or the
# filename in a path expression. Following test_meditation_stop_path_authority's
# precedent, a human-readable message that merely names the file (promote prints
# "MEDITATION_STATE.json is not written") is not a wiring reference.
_STATE_PATH_USE = re.compile(
    r'\$\{?STATE_FILE\}?|[/\\]\s*"?MEDITATION_STATE\.json'
)

# The two shapes the old, orphaned implementation used. Guards the guard: if the
# regex above rots, the static test would pass no matter what crept back in.
_HISTORICAL_OFFENDERS = [
    '  [[ -f "$STATE_FILE" ]]    || _err "MEDITATION_STATE.json not found: $STATE_FILE"',
    'state_path = _REPO / "state" / "MEDITATION_STATE.json"',
]


def _cmd_promote_body() -> list[str]:
    lines = RONIN.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("cmd_promote()"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("}"))
    return [
        l for l in lines[start:end]
        if l.strip() and not l.strip().startswith("#")
    ]


def test_promote_path_never_writes_meditation_state() -> None:
    """Static guard against re-divergence: the executable body of cmd_promote must
    not resolve MEDITATION_STATE.json as a path. A behavioral test only proves the
    current inputs; this stops the old destination being reintroduced alongside
    the new one."""
    offenders = [l.strip() for l in _cmd_promote_body() if _STATE_PATH_USE.search(l)]
    assert not offenders, (
        "cmd_promote references MEDITATION_STATE.json as a path:\n  "
        + "\n  ".join(offenders)
        + "\nPromoted items belong in PROPOSED_BACKLOG.json, which the /goal sweep reads."
    )


def test_static_guard_detects_the_old_implementation() -> None:
    """The detector must fire on the code it exists to keep out."""
    for line in _HISTORICAL_OFFENDERS:
        assert _STATE_PATH_USE.search(line), f"detector missed a known offender: {line}"


def test_promote_body_still_targets_the_sweep_source() -> None:
    """And it must positively reference the file the sweep reads — otherwise the
    guard above passes for a promote that writes nowhere at all."""
    body = "\n".join(_cmd_promote_body())
    assert "$PROPOSED_FILE" in body, body
    assert "$PROMOTED_STATUS" in body, body
    assert f'PROMOTED_STATUS="{PROMOTED_STATUS}"' in RONIN.read_text(encoding="utf-8"), (
        f"bin/ronin no longer defines PROMOTED_STATUS as {PROMOTED_STATUS!r} — that "
        "string is the contract with the /goal sweep."
    )


def test_sweep_source_path_matches_the_skill_contract() -> None:
    """The /goal skill hardcodes the absolute path it sweeps. If that path stops
    naming this file, promotion is orphaned again — and the failure would be
    silent, so pin it here."""
    skill = Path.home() / ".claude" / "skills" / "goal" / "SKILL.md"
    if not skill.exists():
        pytest.skip(f"{skill} not present (public/flat tree)")
    text = skill.read_text(encoding="utf-8")
    assert SWEEP_SOURCE_RELATIVE in text, (
        f"the /goal sweep no longer names {SWEEP_SOURCE_RELATIVE}; "
        "ronin promote's destination must follow it."
    )


def test_promote_skips_ratified_items_whose_work_already_shipped(tmp_path: Path) -> None:
    """`approved: true` is the ratification record and is never cleared, so it stays
    true after the work ships. Promoting on that flag alone re-queues finished work
    into the nightly sweep. Measured 2026-08-16: of 7 ratified items, 3 were already
    `implemented` and 1 `staged`, and a bare promote would have flipped all four."""
    repo = _make_repo(
        tmp_path,
        [
            {"id": "LEDGER-001", "title": "Not yet worked", "status": "proposed",
             "approved": True},
            {"id": "ADOPT-001", "title": "Already built", "status": "implemented",
             "approved": True},
            {"id": "ADOPT-004", "title": "Blocked on the release lane",
             "status": "staged", "approved": True},
            {"id": "LAND-001", "title": "Already shipped", "status": "shipped",
             "approved": True},
        ],
    )
    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    by_id = {i["id"]: i for i in _proposed(repo)}
    assert by_id["LEDGER-001"]["status"] == PROMOTED_STATUS
    assert by_id["LEDGER-001"].get("promoted_at")

    for stale_id, keeps in (("ADOPT-001", "implemented"),
                            ("ADOPT-004", "staged"),
                            ("LAND-001", "shipped")):
        assert by_id[stale_id]["status"] == keeps, (
            f"{stale_id} was re-queued into the sweep despite its work being {keeps}"
        )
        assert "promoted_at" not in by_id[stale_id], (
            f"{stale_id} was stamped as promoted without being promotable"
        )

    # The skip must be visible, not silent — a quiet filter trades one silence bug
    # for another.
    assert "skipping 3 ratified item(s)" in proc.stdout, proc.stdout
    for stale_id in ("ADOPT-001", "ADOPT-004", "LAND-001"):
        assert stale_id in proc.stdout, proc.stdout


def test_promote_is_idempotent_across_repeated_runs(tmp_path: Path) -> None:
    """A second promote must not re-stamp an item the first one already moved."""
    repo = _make_repo(
        tmp_path,
        [{"id": "AUTO-200", "title": "Wire Z", "status": "proposed", "approved": True}],
    )
    assert _run(repo, "promote").returncode == 0
    first = _proposed(repo)[0]["promoted_at"]

    proc = _run(repo, "promote")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _proposed(repo)[0]["promoted_at"] == first, "re-promotion re-stamped the item"
    assert "No new approved items" in proc.stdout, proc.stdout
