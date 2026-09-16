from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from execution import verify_incident_coverage as verifier
from execution.verify_incident_coverage import Catalog, analyze, run_checks, run_doctor_checks


NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


@pytest.fixture
def catalog() -> Catalog:
    return Catalog(
        metrics={"Error_Rate"},
        verifiers={"verify_incident_coverage"},
        doctor_families={"factory"},
        hooks={"protected-asset-gate"},
        git_hooks={"pre-commit"},
    )


def _doc(path: Path, header: str, body: str = "# Incident\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{header.strip()}\n---\n\n{body}", encoding="utf-8")
    return path


def _report(root: Path, catalog: Catalog, memory: Path | None = None) -> dict:
    memory = memory or root / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    return analyze(
        solution_roots=(root / "solutions",), memory_root=memory,
        catalog=catalog, now=NOW,
    )


@pytest.mark.parametrize(("kind", "item_id"), [
    ("metric", "Error_Rate"),
    ("verifier", "verify_incident_coverage"),
    ("doctor_family", "factory"),
    ("hook", "protected-asset-gate"),
    ("git_hook", ".githooks/pre-commit"),
])
def test_each_instrument_kind_must_resolve(
    tmp_path: Path, catalog: Catalog, kind: str, item_id: str,
):
    _doc(tmp_path / "solutions" / "covered.md", f"""
component: test
covered_by:
  - kind: {kind}
    id: {item_id}
""")
    report = _report(tmp_path, catalog)
    assert report["covered"] == 1
    assert report["uncovered"] == []


def test_unresolvable_id_is_uncovered(tmp_path: Path, catalog: Catalog):
    _doc(tmp_path / "solutions" / "bad.md", """
component: hooks
covered_by:
  - kind: hook
    id: missing-hook
""")
    report = _report(tmp_path, catalog)
    assert report["uncovered"][0]["group"] == "hooks"
    assert "does not resolve" in report["uncovered"][0]["reason"]


@pytest.mark.parametrize(("kind", "item_id"), [
    ("verifier", "/tmp/verify_incident_coverage.py"),
    ("verifier", "elsewhere/verify_incident_coverage.py"),
    ("verifier", "../Governance/Order Samurai/execution/verify_incident_coverage.py"),
    ("git_hook", "/tmp/pre-commit"),
    ("git_hook", "elsewhere/pre-commit"),
])
def test_instrument_paths_cannot_escape_authoritative_directories(
    tmp_path: Path, catalog: Catalog, kind: str, item_id: str,
):
    _doc(tmp_path / "solutions" / "bad-path.md", f"""
component: paths
covered_by:
  - kind: {kind}
    id: {item_id}
""")
    assert len(_report(tmp_path, catalog)["uncovered"]) == 1


@pytest.mark.parametrize("header", [
    "disposition: out-of-scope\nlayer: third-party-tool",
    "disposition: accepted-workaround\ncost: one manual check per release",
])
def test_complete_disposition_shapes_are_covered(
    tmp_path: Path, catalog: Catalog, header: str,
):
    _doc(tmp_path / "solutions" / "disposed.md", header)
    assert _report(tmp_path, catalog)["covered"] == 1


@pytest.mark.parametrize("header", [
    "disposition: out-of-scope",
    "disposition: accepted-workaround",
    "disposition: pending\nlayer: runtime",
])
def test_incomplete_or_pending_disposition_is_uncovered(
    tmp_path: Path, catalog: Catalog, header: str,
):
    _doc(tmp_path / "solutions" / "bad-disposition.md", header)
    assert len(_report(tmp_path, catalog)["uncovered"]) == 1


def test_only_recent_typed_memory_is_in_scope(tmp_path: Path, catalog: Catalog):
    memory = tmp_path / "memory"
    recent = (NOW - timedelta(days=89)).isoformat().replace("+00:00", "Z")
    old = (NOW - timedelta(days=91)).isoformat().replace("+00:00", "Z")
    _doc(memory / "recent.md", f"""
metadata:
  type: feedback
  modified: {recent}
component: harness
""")
    _doc(memory / "old.md", f"""
metadata:
  type: project
  modified: {old}
component: harness
""")
    _doc(memory / "reference.md", f"""
metadata:
  type: reference
  modified: {recent}
""")
    report = _report(tmp_path, catalog, memory)
    assert report["scanned"] == 1
    assert report["uncovered"][0]["path"].endswith("recent.md")


def test_typed_memory_with_bad_modified_timestamp_is_unmeasured(tmp_path: Path, catalog: Catalog):
    memory = tmp_path / "memory"
    _doc(memory / "bad-date.md", """
metadata:
  type: feedback
  modified: not-a-date
""")
    report = _report(tmp_path, catalog, memory)
    assert report["scanned"] == 0
    assert any("bad-date.md" in item and "unparseable modified" in item
               for item in report["unmeasured"])


def test_missing_memory_directory_warns_unmeasured(tmp_path: Path, catalog: Catalog):
    solutions = tmp_path / "solutions"
    _doc(solutions / "covered.md", "disposition: out-of-scope\nlayer: external")
    missing = tmp_path / "missing-memory"
    rows = run_checks(
        solution_roots=(solutions,), memory_root=missing, catalog=catalog, now=NOW,
    )
    assert any(row["label"] == "incident-coverage.unmeasured" for row in rows)
    assert not any(row["status"] == "OK" for row in rows)


def test_doctor_lists_only_top_five_uncovered_groups(tmp_path: Path, catalog: Catalog):
    solutions = tmp_path / "solutions"
    for number in range(6):
        _doc(solutions / f"{number}.md", f"component: family-{number}")
    memory = tmp_path / "memory"
    memory.mkdir()
    rows = run_doctor_checks(
        solution_roots=(solutions,), memory_root=memory, catalog=catalog, now=NOW,
    )
    assert len(rows) == 5
    assert all(row["label"] == "incident-coverage.uncovered-family" for row in rows)


def test_doctor_scope_excludes_memory_until_it_has_a_coverage_convention(
    tmp_path: Path, catalog: Catalog,
):
    solutions = tmp_path / "solutions"
    _doc(solutions / "covered.md", "disposition: out-of-scope\nlayer: external")
    memory = tmp_path / "memory"
    recent = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    _doc(memory / "uncovered.md", f"metadata:\n  type: feedback\n  modified: {recent}")
    rows = run_doctor_checks(
        solution_roots=(solutions,), memory_root=memory, catalog=catalog, now=NOW,
    )
    assert rows == [{"status": "OK", "label": "incident-coverage.covered",
                     "detail": "all 1 in-scope document(s) have coverage"}]


def test_json_report_is_serializable(tmp_path: Path, catalog: Catalog):
    _doc(tmp_path / "solutions" / "bad.md", "component: test")
    assert json.loads(json.dumps(_report(tmp_path, catalog)))["uncovered"]


def test_cli_is_advisory_unless_strict(monkeypatch, capsys):
    report = {
        "scanned": 1,
        "covered": 0,
        "uncovered": [{"path": "incident.md", "group": "git", "reason": "missing"}],
        "groups": {"git": 1},
        "unmeasured": [],
    }
    monkeypatch.setattr(verifier, "analyze", lambda: report)
    assert verifier.main([]) == 0
    assert "1 uncovered, 0 unmeasured" in capsys.readouterr().out
    assert verifier.main(["--strict"]) == 1
    capsys.readouterr()
    assert verifier.main(["--json"]) == 0
    # --json prints exactly one JSON document; parse the whole capture rather than
    # guessing its line count (the first version indexed a fixed line and broke).
    assert json.loads(capsys.readouterr().out)["scanned"] == 1
