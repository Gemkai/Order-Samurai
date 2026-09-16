"""Regression coverage for verify_falsifiability.py, focused on
check_matrix_registry_drift's standalone-export fallback.

Found 2026-08-31 (code review): the fallback matched literal test-fixture
strings ("FakeMetric"/"NON_EXISTENT_METRIC") or the fixture directory being
named "bad" -- not real content. The actual bad/ fixture doesn't even contain
those substrings, so it only "passed" via the directory-name shortcut; any
real standalone-export drift not spelled exactly that way would have silently
reported clean. These tests exercise the STANDALONE code path directly (by
calling the private standalone helpers the real fallback uses) against inputs
the old string-matching version could not have told apart from clean.

Hermetic by design: `Governance/dashboard-ui/public/wid_payload.json` is
gitignored (a locally-generated runtime artifact -- `refresh_dashboard.py`
regenerates it, it's never committed), so these tests never read the real
repo-path payload. Every test builds its own temp `_OS_ROOT`/payload tree via
`_fake_os_root()` so results don't depend on whether the running environment
happens to have a generated payload on disk.
"""
from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docs"))

import verify_falsifiability as vf  # noqa: E402


def _fake_os_root(tmp_path: Path, payload_pillars: dict | None) -> Path:
    """Build <tmp>/os_root (the fake _OS_ROOT) and, when payload_pillars is not
    None, <tmp>/dashboard-ui/public/wid_payload.json alongside it -- mirroring
    the real relative layout _OS_ROOT.parent / 'dashboard-ui' / 'public' /
    'wid_payload.json' expects. Passing payload_pillars=None simulates no
    generated payload existing at all (the common local/CI case)."""
    os_root = tmp_path / "os_root"
    os_root.mkdir()
    if payload_pillars is not None:
        payload_dir = tmp_path / "dashboard-ui" / "public"
        payload_dir.mkdir(parents=True)
        (payload_dir / "wid_payload.json").write_text(
            json.dumps({"pillars": payload_pillars}), encoding="utf-8"
        )
    return os_root


def _standalone_drift(target_dir: Path, os_root: Path) -> tuple[bool, str]:
    """Call the standalone fallback's own logic directly, with a caller-supplied
    _OS_ROOT (see _fake_os_root) -- this is exactly what the except-ImportError
    branch in check_matrix_registry_drift executes."""
    matrix_file = target_dir / "metrics_remediation_matrix.md"
    if not matrix_file.is_file():
        return False, "no metrics_remediation_matrix.md fixture found"
    payload_path = os_root.parent / "dashboard-ui" / "public" / "wid_payload.json"
    if not payload_path.is_file():
        return False, f"cannot verify: no live registry payload at {payload_path}"
    text = matrix_file.read_text(encoding="utf-8")
    live_roster = vf._standalone_load_live_roster(payload_path)
    in_matrix_not_live, live_not_in_matrix = vf._standalone_compute_drift(text, live_roster)
    if in_matrix_not_live or live_not_in_matrix:
        return False, f"in_matrix_not_live={in_matrix_not_live} live_not_in_matrix={live_not_in_matrix}"
    return True, "matrix matches live registry"


_MATRIX_TEMPLATE = (
    "# Metrics\n\n"
    "<!-- GENERATED:ROSTER:BOW:START -->\n"
    "{bow_rows}"
    "<!-- GENERATED:ROSTER:BOW:END -->\n\n"
    "<!-- GENERATED:ROSTER:SWORD:START -->\n"
    "| **Existing_Metric** | Ops | Graded | `x` | `/x` | auto |\n"
    "<!-- GENERATED:ROSTER:SWORD:END -->\n\n"
    "<!-- GENERATED:ROSTER:BRUSH:START -->\n"
    "<!-- GENERATED:ROSTER:BRUSH:END -->\n\n"
    "<!-- GENERATED:ROSTER:ARTS:START -->\n"
    "<!-- GENERATED:ROSTER:ARTS:END -->\n"
)

_LIVE_ROSTER = {
    "bow": {"ops": ["Existing_Metric"]},
    "sword": {"ops": ["Existing_Metric"]},
    "brush": {},
    "arts": {},
}


def test_standalone_fallback_passes_when_matrix_matches_live_roster(tmp_path):
    os_root = _fake_os_root(tmp_path, _LIVE_ROSTER)
    target = tmp_path / "clean_export"
    target.mkdir()
    (target / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=""), encoding="utf-8"
    )
    ok, _detail = _standalone_drift(target, os_root)
    assert ok is True


def test_standalone_fallback_catches_drift_the_old_string_match_would_have_missed(tmp_path):
    """The regression proof: a directory NOT named 'bad', containing a genuinely
    drifted metric whose name is neither 'FakeMetric' nor 'NON_EXISTENT_METRIC'
    -- the exact shape of input the OLD implementation silently reported clean
    for (it only ever failed via `target_dir.name == "bad"` or those two exact
    substrings)."""
    os_root = _fake_os_root(tmp_path, _LIVE_ROSTER)
    target = tmp_path / "export_2"  # deliberately NOT named "bad"
    target.mkdir()
    bow_rows = "| **Totally_Unrelated_Ghost_Metric** | Test | Graded | `x` | `/x` | advisory |\n"
    (target / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=bow_rows), encoding="utf-8"
    )

    ok, detail = _standalone_drift(target, os_root)
    assert ok is False, (
        "a real drifted metric not named 'FakeMetric'/'NON_EXISTENT_METRIC', in a "
        "directory not named 'bad', must still be caught"
    )
    assert "Totally_Unrelated_Ghost_Metric" in detail


def test_standalone_fallback_catches_a_live_metric_missing_from_the_matrix(tmp_path):
    """The other drift direction: a live metric the matrix never mentions at
    all. Old code never looked at this direction either."""
    live = {"bow": {"ops": ["Existing_Metric", "Brand_New_Live_Metric"]},
            "sword": {}, "brush": {}, "arts": {}}
    os_root = _fake_os_root(tmp_path, live)
    target = tmp_path / "export_3"
    target.mkdir()
    # Matrix mentions only Existing_Metric -- Brand_New_Live_Metric is live but
    # never documented, the "live_not_in_matrix" drift direction.
    (target / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=""), encoding="utf-8"
    )
    ok, detail = _standalone_drift(target, os_root)
    assert ok is False
    assert "Brand_New_Live_Metric" in detail


def test_standalone_fallback_fails_closed_when_no_live_payload_available(tmp_path):
    """A verifier that cannot see ground truth must never silently report clean."""
    os_root = _fake_os_root(tmp_path, payload_pillars=None)
    target = tmp_path / "no_payload_here"
    target.mkdir()
    (target / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=""), encoding="utf-8"
    )
    ok, detail = _standalone_drift(target, os_root)
    assert ok is False
    assert "cannot verify" in detail


def test_the_shipped_bad_fixture_and_clean_fixture_are_a_real_falsifiable_pair(tmp_path):
    """End-to-end: the actual fixture pair shipped in
    tests/falsifiability_fixtures/matrix_registry_drift/, run through the
    STANDALONE path with a controlled payload matching what the fixtures were
    generated against. Proves the shipped fixtures themselves (not just
    synthetic inputs above) are real content, not directory-name theater."""
    fixtures = vf.FIXTURES_ROOT / "matrix_registry_drift"
    clean_text = (fixtures / "clean" / "metrics_remediation_matrix.md").read_text(encoding="utf-8")
    bad_text = (fixtures / "bad" / "metrics_remediation_matrix.md").read_text(encoding="utf-8")

    # Real regen_metrics_matrix.compute_drift() against the clean fixture with
    # itself as the "live roster" source is definitionally driftless -- derive
    # a live-roster payload from the clean fixture's OWN metric names (all four
    # pillar blocks, not just one) so this test needs no import of the real
    # (agentica_core-coupled) module at all.
    import re
    live: dict[str, dict[str, list[str]]] = {}
    for pillar in ("bow", "sword", "brush", "arts"):
        block = re.search(
            rf"<!-- GENERATED:ROSTER:{pillar.upper()}:START -->(.*?)"
            rf"<!-- GENERATED:ROSTER:{pillar.upper()}:END -->",
            clean_text, re.DOTALL,
        )
        metrics = re.findall(r"\*\*([A-Za-z0-9_]+)\*\*", block.group(1)) if block else []
        live[pillar] = {"live": metrics} if metrics else {}
    assert any(live.values()), "clean fixture has no metrics in any pillar -- fixture may be stale"

    os_root = _fake_os_root(tmp_path, live)

    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    (clean_dir / "metrics_remediation_matrix.md").write_text(clean_text, encoding="utf-8")
    ok_clean, _ = _standalone_drift(clean_dir, os_root)
    assert ok_clean is True, "shipped clean/ fixture must pass against its own metric set"

    bad_dir = tmp_path / "bad_but_not_named_bad"
    bad_dir.mkdir()
    (bad_dir / "metrics_remediation_matrix.md").write_text(bad_text, encoding="utf-8")
    ok_bad, detail_bad = _standalone_drift(bad_dir, os_root)
    assert ok_bad is False, (
        "shipped bad/ fixture must fail even in a directory NOT named 'bad' -- "
        "proves the check no longer depends on the directory-name shortcut"
    )
    assert "Fake_Nonexistent_Metric" in detail_bad


def test_check_matrix_registry_drift_itself_takes_the_standalone_branch_on_importerror(
    tmp_path, monkeypatch
):
    """Integration test for the actual production entry point, not just the
    private helpers: forces the real ImportError check_matrix_registry_drift's
    try/except is built around, and asserts the PUBLIC function (its own
    try/except wiring, not a hand-rolled test reimplementation of it) behaves
    correctly end-to-end against both a bad and a clean fixture."""
    os_root = _fake_os_root(tmp_path, _LIVE_ROSTER)
    monkeypatch.setattr(vf, "_OS_ROOT", os_root)

    real_import = __import__
    def blocked_import(name, *a, **kw):
        if name == "regen_metrics_matrix":
            raise ImportError("simulated standalone distribution")
        return real_import(name, *a, **kw)
    monkeypatch.setattr("builtins.__import__", blocked_import)

    clean_dir = tmp_path / "clean_export"
    clean_dir.mkdir()
    (clean_dir / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=""), encoding="utf-8"
    )
    ok_clean, _ = vf.check_matrix_registry_drift(clean_dir)
    assert ok_clean is True

    bad_dir = tmp_path / "bad_export"
    bad_dir.mkdir()
    bow_rows = "| **Ghost_Metric** | Test | Graded | `x` | `/x` | advisory |\n"
    (bad_dir / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=bow_rows), encoding="utf-8"
    )
    ok_bad, detail_bad = vf.check_matrix_registry_drift(bad_dir)
    assert ok_bad is False
    assert "Ghost_Metric" in detail_bad


def test_standalone_compute_drift_matches_the_real_regen_metrics_matrix_module():
    """The reimplementation must stay behaviorally identical to the real
    regen_metrics_matrix.compute_drift() it stands in for when that module's
    import is blocked. Skips (does not fail) if the real module can't be
    imported here -- that ImportError is the exact condition being simulated,
    so being unable to cross-check it in THIS environment is not a test failure."""
    try:
        import regen_metrics_matrix as rmm
    except ImportError:
        import pytest
        pytest.skip("regen_metrics_matrix genuinely unimportable in this environment")

    matrix_text = _MATRIX_TEMPLATE.format(
        bow_rows="| **Extra_Metric** | Test | Graded | `x` | `/x` | advisory |\n"
    )
    real_result = rmm.compute_drift(matrix_text, _LIVE_ROSTER)
    standalone_result = vf._standalone_compute_drift(matrix_text, _LIVE_ROSTER)
    assert real_result == standalone_result


# ------------------------------------------------- main(): --json flag + exit code
#
# Found 2026-08-31/09-01 (code review, P1 self-follow-up): main() had no --json
# support at all -- reconcile_state.py's stage_3 calls this script with --json
# expecting machine-readable output but silently got the human text format
# instead (no argparse existed to reject the unrecognized flag; it was just
# ignored). Separately, main() always returned 0 regardless of results, so a
# caller checking only the exit code (exactly what reconcile_state.py's stage_3
# does) could never see a real falsifiability failure here.


def _stub_check_pair(tmp_path, name, *, bad_ok, clean_ok):
    """A synthetic registered check with fully controlled bad/clean outcomes,
    injected via vf.CHECKS/vf.FIXTURES_ROOT so these tests never touch the
    real repo's fixtures or depend on live repo state."""
    (tmp_path / name / "bad").mkdir(parents=True)
    (tmp_path / name / "clean").mkdir(parents=True)

    def _check(target_dir: Path) -> tuple[bool, str]:
        is_bad = target_dir.name == "bad"
        return (bad_ok if is_bad else clean_ok), "stub"

    return _check


def test_main_json_flag_emits_valid_parseable_json(monkeypatch, capsys, tmp_path):
    check = _stub_check_pair(tmp_path, "stub_check", bad_ok=False, clean_ok=True)
    monkeypatch.setattr(vf, "CHECKS", {"stub_check": check})
    monkeypatch.setattr(vf, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify_falsifiability.py", "--json"])

    exit_code = vf.main()

    out = capsys.readouterr().out
    parsed = json.loads(out)   # must not raise -- this is the whole point of the fix
    assert "falsifiable" in parsed and "total" in parsed and "checks" in parsed
    assert parsed["checks"]["stub_check"]["status"] == "pass"
    assert exit_code == 0


def test_without_json_flag_output_is_still_the_human_text_format(monkeypatch, capsys, tmp_path):
    """Regression guard: the default (no --json) behavior must not change --
    only the new flag adds a code path, nothing existing shifts."""
    check = _stub_check_pair(tmp_path, "stub_check", bad_ok=False, clean_ok=True)
    monkeypatch.setattr(vf, "CHECKS", {"stub_check": check})
    monkeypatch.setattr(vf, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify_falsifiability.py"])

    vf.main()

    out = capsys.readouterr().out
    assert out.startswith("Verifier_Falsifiability:")
    assert "stub_check: pass" in out


def test_main_exit_code_is_nonzero_when_a_registered_check_genuinely_fails(monkeypatch, tmp_path):
    """The core regression proof: a stub whose 'bad' fixture wrongly passes
    (a real falsifiability failure -- the check can't tell bad from clean)
    must make main() exit nonzero. The OLD code returned 0 unconditionally."""
    check = _stub_check_pair(tmp_path, "broken_check", bad_ok=True, clean_ok=True)
    monkeypatch.setattr(vf, "CHECKS", {"broken_check": check})
    monkeypatch.setattr(vf, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify_falsifiability.py"])

    assert vf.main() == 1


def test_main_exit_code_stays_zero_when_only_coverage_is_incomplete(monkeypatch, capsys, tmp_path):
    """An UNTESTED check (no fixture pair registered) must never force a
    nonzero exit on its own -- that would demand 100% fixture coverage of
    every discovered verify_*.py script just to exit clean, which is a much
    stronger (and wrong) bar than 'no registered check is broken'. Register
    one check WITH a passing fixture pair and rely on real discovered scripts
    (which have no fixture pairs at all) to supply the untested gap."""
    check = _stub_check_pair(tmp_path, "stub_check", bad_ok=False, clean_ok=True)
    monkeypatch.setattr(vf, "CHECKS", {"stub_check": check})
    monkeypatch.setattr(vf, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["verify_falsifiability.py", "--json"])

    exit_code = vf.main()

    out = json.loads(capsys.readouterr().out)
    assert out["total"] > out["falsifiable"], (
        "this test is only meaningful if there's a real coverage gap to prove "
        "doesn't affect the exit code -- if it stops being true, discovered "
        "verify_*.py scripts must have grown fixture pairs and this fixture needs updating"
    )
    assert exit_code == 0


def test_check_matrix_registry_drift_reports_a_missing_payload_instead_of_raising(
    tmp_path, monkeypatch
):
    """The PRIMARY (regen_metrics_matrix importable) branch must fail closed the
    same way the standalone fallback does. Found 2026-09-02: on any checkout
    without the gitignored wid_payload.json -- every fresh clone, every CI
    runner -- load_live_roster() raised FileNotFoundError straight through
    run_falsifiability(), so `verify_falsifiability.py` died with a traceback
    and no Summary line rather than reporting a FAIL doctor.py can parse."""
    rmm = pytest.importorskip("regen_metrics_matrix")
    missing = tmp_path / "dashboard-ui" / "public" / "wid_payload.json"
    monkeypatch.setattr(rmm, "_PAYLOAD_PATH", missing)

    clean_dir = tmp_path / "clean_export"
    clean_dir.mkdir()
    (clean_dir / "metrics_remediation_matrix.md").write_text(
        _MATRIX_TEMPLATE.format(bow_rows=""), encoding="utf-8"
    )
    ok, detail = vf.check_matrix_registry_drift(clean_dir)
    assert ok is False
    assert "cannot verify" in detail
    assert str(missing) in detail
