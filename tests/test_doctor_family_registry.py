"""doctor.py's check-family registry (M7.1).

main() used to run, print, summarize and hand-sum 17 families across four
parallel blocks — a new family meant four edits in four places, and an omission
was invisible in the output. CHECK_FAMILIES declares each family once.

These tests pin what the refactor had to preserve exactly: the print order, the
per-family counting rules (including two that are NOT uniform), the exit-code
semantics, and the one family that reports its label under a different key.
Counting is asserted against synthetic families so a rule change fails here
rather than only on a machine whose live checks happen to expose it.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from execution import doctor  # noqa: E402


def _family(name, results, **kw):
    return doctor._Family(name, lambda: results, kw.pop("gating", False), **kw)


def _row(status, detail="d", key="label", label="x"):
    return {"status": status, key: label, "detail": detail}


# ── the registry itself ───────────────────────────────────────────────────────

#: The doctor's published output order. Frozen from the pre-refactor main(),
#: which ran these in exactly this sequence.
EXPECTED_ORDER = [
    "path-authority", "stale-paths", "live-sources", "runtime-contract",
    "root-hygiene", "agentica-root-hygiene", "archive-boundaries",
    "meditation-timestamps", "schema-violations", "local-llm",
    "container-services", "audit-gate-canary", "telemetry-freshness",
    "claude-telemetry", "exec-chain", "factory", "claude-architecture",
]


def test_family_order_is_unchanged():
    assert [f.name for f in doctor.CHECK_FAMILIES] == EXPECTED_ORDER


def test_every_family_is_callable():
    for family in doctor.CHECK_FAMILIES:
        assert callable(family.runner), family.name


def test_the_gating_families_are_exactly_the_ones_that_fail_doctor():
    """These eleven feed the exit code; the rest are spectators by design."""
    gating = {f.name for f in doctor.CHECK_FAMILIES if f.gating}
    assert gating == {
        "path-authority", "stale-paths", "live-sources", "runtime-contract",
        "root-hygiene", "agentica-root-hygiene", "archive-boundaries",
        "telemetry-freshness", "claude-telemetry", "exec-chain",
        "claude-architecture",
    }


def test_telemetry_and_exec_chain_remain_gates():
    """Named explicitly: both were made gates after real silent-failure incidents
    (a 15-day dead emitter; an edited remediation verdict). A future refactor
    demoting either to a spectator must fail here."""
    by_name = {f.name: f for f in doctor.CHECK_FAMILIES}
    assert by_name["telemetry-freshness"].gating is True
    assert by_name["exec-chain"].gating is True


def test_only_agentica_root_hygiene_uses_the_name_key():
    odd = {f.name for f in doctor.CHECK_FAMILIES if f.label_key != "label"}
    assert odd == {"agentica-root-hygiene"}


def test_only_claude_telemetry_suppresses_its_warn_count():
    """Preserved verbatim from the pre-refactor summing, which never added
    claude-telemetry's WARN rows to the total."""
    odd = {f.name for f in doctor.CHECK_FAMILIES if not f.warns}
    assert odd == {"claude-telemetry"}


# ── counting and exit semantics ───────────────────────────────────────────────

def test_results_are_printed_in_family_order():
    families = (
        _family("first", [_row("OK", label="a")]),
        _family("second", [_row("OK", label="b")]),
    )

    lines, _, _ = doctor.run_families(families)

    assert lines == ["[OK] a: d", "[OK] b: d"]


def test_a_gating_family_failure_counts_and_sets_the_exit_code():
    families = (_family("gate", [_row("FAIL")], gating=True),)

    _, counts, exit_code = doctor.run_families(families)

    assert counts["FAIL"] == 1
    assert exit_code == 1


def test_a_spectator_family_failure_neither_counts_nor_gates():
    """A live-workstation probe must not fail the run; each such family's own
    comment states why it is a spectator."""
    families = (_family("probe", [_row("FAIL")], gating=False),)

    _, counts, exit_code = doctor.run_families(families)

    assert counts["FAIL"] == 0
    assert exit_code == 0


def test_a_suppressed_warn_family_still_reports_its_row():
    """warns=False affects the COUNT only — the row is still printed, or the
    operator would lose the finding entirely."""
    families = (_family("quiet", [_row("WARN")], gating=True, warns=False),)

    lines, counts, _ = doctor.run_families(families)

    assert counts["WARN"] == 0
    assert lines == ["[WARN] x: d"]


def test_warns_are_counted_for_every_other_family():
    families = (_family("loud", [_row("WARN")]),)

    _, counts, exit_code = doctor.run_families(families)

    assert counts["WARN"] == 1
    assert exit_code == 0


def test_the_alternate_label_key_is_honoured():
    families = (_family("odd", [_row("OK", key="name", label="named")],
                        label_key="name"),)

    lines, _, _ = doctor.run_families(families)

    assert lines == ["[OK] named: d"]


def test_counts_accumulate_across_families():
    families = (
        _family("a", [_row("OK"), _row("WARN")]),
        _family("b", [_row("OK"), _row("FAIL")], gating=True),
    )

    _, counts, exit_code = doctor.run_families(families)

    assert counts == {"OK": 2, "WARN": 1, "FAIL": 1}
    assert exit_code == 1


def test_an_empty_family_contributes_nothing():
    families = (_family("silent", []),)

    lines, counts, exit_code = doctor.run_families(families)

    assert lines == []
    assert counts == {"OK": 0, "WARN": 0, "FAIL": 0}
    assert exit_code == 0
