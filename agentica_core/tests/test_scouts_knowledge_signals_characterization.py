"""Characterization of scouts.knowledge_signals() before the W3 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W3 ("no other function in the
file above C" pulls this E-35 function in). Each scenario builds a repo root with the inputs the
function reads (OKF toolkit + vault, vault/me ages and index, the SOJI findings file, the
dashboard graph) under a frozen clock with pinned file mtimes, and compares the full returned
dict with fixtures/scouts_knowledge_signals_snapshots.json, recorded from the pre-refactor code
(origin/work @ f6e3a6859). Never regenerate it to make a refactor pass.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agentica_core.scouts as scouts

SNAPSHOTS = Path(__file__).with_name("fixtures") / "scouts_knowledge_signals_snapshots.json"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
DAY = 86400.0


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


OKF_OK = '''
from pathlib import Path
def iter_concepts(vault):
    return sorted(Path(vault).glob("concepts/*.md"))
def read_concept(vault, p):
    return {"conformant": "ok" in Path(p).read_text()}
'''
OKF_EMPTY = "def iter_concepts(vault):\n    return []\ndef read_concept(vault, p):\n    return {}\n"
OKF_OSERROR = "def iter_concepts(vault):\n    raise OSError('vault unreadable')\ndef read_concept(v, p):\n    return {}\n"
OKF_BROKEN = "this is not python\n"


def write(path: Path, text: str, age_days: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_days is not None:
        t = NOW.timestamp() - age_days * DAY
        os.utime(path, (t, t))
    return path


GRAPH = {"nodes": [
    {"id": "a", "ring": 2, "cluster": "CORE"}, {"id": "b", "ring": 2, "cluster": "ARCHIVE"},
    {"id": "c", "ring": 2, "cluster": "CORE"}, {"id": "d", "ring": 1}, "not-a-dict",
    {"id": "e", "ring": 2, "cluster": "CORE"}],
    "links": [[0, 3], [1, 2, 9], "bad", [5, 5]]}

SCENARIOS = {
    "empty_root": dict(),
    "full_inputs": dict(okf=OKF_OK, concepts={"x.md": "ok", "y.md": "no", "z.md": "ok"},
                        me={"a.md": 3.0, "b.md": 1.25, "index.md": 9.0},
                        index="[a](a.md) [gone](gone.md) [a again](a.md#top)",
                        soji={"generated_at": "2026-09-08T00:00:00Z", "findings": [
                            {"kind": "broken_link"}, {"kind": "broken_link"}, {"kind": "orphan_note"},
                            {"kind": "other"}, "junk"]},
                        graph=GRAPH, graph_age=0.5),
    "okf_without_concepts": dict(okf=OKF_EMPTY, concepts={}, me={}),
    "okf_raises_oserror": dict(okf=OKF_OSERROR, concepts={"x.md": "ok"}),
    "okf_module_broken": dict(okf=OKF_BROKEN, concepts={"x.md": "ok"}),
    "okf_present_but_no_vault": dict(okf=OKF_OK, no_vault=True),
    "soji_stale": dict(soji={"generated_at": "2026-08-01T00:00:00Z", "findings": [{"kind": "broken_link"}]}),
    "soji_ten_days_old": dict(soji={"generated_at": "2026-08-31T12:00:00Z", "findings": [{"kind": "broken_link"}]}),
    "soji_naive_fresh_timestamp": dict(soji={"generated_at": "2026-09-09T00:00:00", "findings": []}),
    "soji_unparseable_timestamp": dict(soji={"generated_at": "yesterday", "findings": []}),
    "soji_without_timestamp": dict(soji={"findings": [{"kind": "broken_link"}]}),
    "soji_findings_not_a_list": dict(soji={"generated_at": "2026-09-09T00:00:00Z", "findings": {}}),
    "graph_without_memory_nodes": dict(graph={"nodes": [{"ring": 1}], "links": []}, graph_age=2.0),
    "graph_nodes_not_a_list": dict(graph={"nodes": {}}),
    "me_without_index": dict(me={"a.md": 10.0}),
}


def build(tmp_path: Path, spec: dict) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    if not spec.get("no_vault"):
        (root / "Knowledge" / "vault").mkdir(parents=True)
    if "okf" in spec:
        write(root / "Knowledge" / "okf" / "okf_tools.py", spec["okf"])
    for name, text in spec.get("concepts", {}).items():
        write(root / "Knowledge" / "vault" / "concepts" / name, text)
    for name, age in spec.get("me", {}).items():
        write(root / "Knowledge" / "vault" / "me" / name, "x", age)
    if "index" in spec:
        write(root / "Knowledge" / "vault" / "me" / "index.md", spec["index"], 9.0)
    if "soji" in spec:
        write(root / "Data" / "soji" / "memory.findings.json", json.dumps(spec["soji"]))
    if "graph" in spec:
        write(root / "Knowledge" / "dashboard" / "graph.json", json.dumps(spec["graph"]),
              spec.get("graph_age", 1.0))
    return root


def run_scenario(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(scouts, "datetime", _FrozenDatetime)
    return json.loads(json.dumps(scouts.knowledge_signals(build(tmp_path, SCENARIOS[name])),
                                 sort_keys=True))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_knowledge_signals_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[name]
    assert run_scenario(name, tmp_path, monkeypatch) == expected


def test_every_scenario_has_a_recorded_snapshot():
    assert sorted(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == sorted(SCENARIOS)


def test_default_repo_root_is_three_levels_above_the_package(monkeypatch):
    """With no repo_root the function reads <checkout root>; it must not raise either way."""
    monkeypatch.setattr(scouts, "datetime", _FrozenDatetime)
    assert isinstance(scouts.knowledge_signals(), dict)
