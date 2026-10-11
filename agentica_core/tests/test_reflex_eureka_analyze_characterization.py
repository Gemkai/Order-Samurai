"""Characterization of reflex_eureka.analyze() before the W4 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W4. analyze is CC 46; these
scenarios pin its return dict, the findings markdown it writes and the lesson-pipeline export
side effects. Expected values live in fixtures/reflex_eureka_analyze_snapshots.json, recorded
from the pre-refactor code (origin/work @ f6e3a6859); never regenerate them to make a
refactor pass. The clock is pinned through `reflex_eureka.datetime`, and every side effect is
redirected with CLAUDE_RUNTIME_ROOT.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentica_core import reflex_eureka as re_mod

SNAPSHOTS = Path(__file__).with_name("fixtures") / "reflex_eureka_analyze_snapshots.json"
NOW = datetime(2026, 9, 2, 12, 30, 45, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


def run(skill, reflex_id, n_effective, n_total, *, ts="2026-09-01T10:00:00Z", **extra):
    rows = []
    for i in range(n_total):
        row = {"source": "reflex_engine", "reflex_id": reflex_id, "skill": skill,
               "timestamp": ts.replace("10:00", f"10:{i:02d}"), "status": "done",
               "improved": i < n_effective}
        row.update(extra)
        rows.append(row)
    return rows


def rich_log() -> list:
    return [
        *run("debug", "metric:bow:Error_Rate", 1, 6),                 # GOTCHA (17%)
        *run("fixer", "metric:sword:Open_CVEs", 0, 5),                # GOTCHA (0%)
        *run("verify", "metric:arts:Slop_Density", 4, 5),             # RULE (80%)
        *run("simplify", "metric:arts:Rework_Loops", 3, 6),           # CONTEXT (50%)
        *run("audit", "correlation:cost_and_quality", 1, 3),          # insufficient (unknown pillar)
        *run("one", "metric:bow:Latency_P95", 1, 1),                  # insufficient, singular
        # Exit-code fallback and read-only mechanism grading (no `improved` key).
        {"source": "reflex_engine", "reflex_id": "metric:bow:Doc_Age", "skill": "docs",
         "timestamp": "2026-09-01T09:00:00Z", "status": "done"},
        {"source": "reflex_engine", "reflex_id": "metric:bow:Doc_Age", "skill": "docs",
         "timestamp": "2026-09-01T09:05:00Z", "status": "error"},
        {"source": "reflex_engine", "reflex_id": "metric:bow:Scan", "kind": "mechanism",
         "read_only": True, "improved": False, "status": "done", "command": "/scan --fast",
         "timestamp": "2026-09-01T08:00:00Z"},
        # Skill derived from the command, or "unknown".
        {"source": "reflex_engine", "reflex_id": "manual:wiki", "command": "   ",
         "timestamp": "2026-09-01T07:00:00Z"},
        {"source": "reflex_engine", "reflex_id": "bad", "timestamp": ""},
        # Filtered out by the loader.
        {"source": "dashboard", "reflex_id": "metric:bow:Error_Rate", "skill": "debug"},
        {"source": "reflex_engine", "skill": "debug"},
        "not-json", "",
    ]


SCENARIOS = {
    "missing_log": dict(log=None),
    "empty_log": dict(log=[]),
    "only_insufficient_pairs": dict(log=run("one", "metric:bow:Latency_P95", 1, 2)),
    "rich_log": dict(log=rich_log()),
    "all_pairs_mature": dict(log=run("verify", "metric:arts:Slop_Density", 5, 5)
                             + run("debug", "metric:bow:Error_Rate", 0, 7)),
    "rich_log_default_out_path": dict(log=rich_log(), default_out=True),
    "rich_log_export_already_current": dict(log=rich_log(), run_twice=True),
    # Added after review (W4a): equal rates sort by total_runs, and a pair exactly at the
    # low-improvement threshold (3/10).
    "tied_rates_and_threshold_boundary": dict(
        log=run("alpha", "metric:bow:Alpha", 1, 5) + run("beta", "metric:bow:Beta", 2, 10)
        + run("gamma", "metric:arts:Gamma", 3, 10)),
}


def run_scenario(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    spec = SCENARIOS[name]
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_RUNTIME_ROOT", str(home))
    monkeypatch.setattr(re_mod, "datetime", _FrozenDatetime)
    monkeypatch.setattr(_FrozenDatetime, "current", NOW)
    log = tmp_path / "exec_log.jsonl"
    if spec["log"] is not None:
        log.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n"
                               for r in spec["log"]), encoding="utf-8")
    out = None if spec.get("default_out") else tmp_path / "out" / "findings.md"
    result = re_mod.analyze(log, out)
    if spec.get("run_twice"):
        # Same instant, same content: the export's sha256 de-dup returns early. (Skipping
        # the de-dup would rewrite byte-identical files, so only coverage sees the branch.)
        result = re_mod.analyze(log, out)
    written = out if out is not None else home / "data" / "auto_eureka_skills.md"
    files = {p.relative_to(home).as_posix(): p.read_text(encoding="utf-8")
             for p in sorted(home.rglob("*")) if p.is_file()} if home.exists() else {}
    return {"result": result, "findings": written.read_text(encoding="utf-8"),
            "side_effect_files": files}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_analyze_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_scenario(name, tmp_path, monkeypatch) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_a_failing_export_never_breaks_analyze(tmp_path, monkeypatch):
    """The lesson-pipeline export is best-effort: an unwritable runtime root is swallowed."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_RUNTIME_ROOT", str(blocker))
    monkeypatch.setattr(re_mod, "datetime", _FrozenDatetime)
    log = tmp_path / "exec_log.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rich_log() if isinstance(r, dict)),
                   encoding="utf-8")
    result = re_mod.analyze(log, tmp_path / "findings.md")
    assert result["rules"] == 1 and (tmp_path / "findings.md").is_file()


def test_a_non_object_json_line_raises_today(tmp_path, monkeypatch):
    """Pinned as-is: _load_exec_log catches JSONDecodeError/TypeError but a JSON array line
    reaches `r.get` and raises AttributeError out of analyze(). A latent bug, out of W4 scope
    (behavior-preserving refactor); recorded for the backlog."""
    monkeypatch.setenv("CLAUDE_RUNTIME_ROOT", str(tmp_path / "home"))
    log = tmp_path / "exec_log.jsonl"
    log.write_text("[1, 2]\n", encoding="utf-8")
    with pytest.raises(AttributeError):
        re_mod.analyze(log, tmp_path / "findings.md")
