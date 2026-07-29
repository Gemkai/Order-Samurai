"""Tests for bin/skill_improvement_scan.py — the deterministic front half of the keiko
skill-improvement cycle. Pure arithmetic over the eval corpus; no LLM, no side effects."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCAN = Path(__file__).resolve().parents[1] / "bin" / "skill_improvement_scan.py"
_spec = importlib.util.spec_from_file_location("skill_improvement_scan", _SCAN)
sis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sis)


def _case(skill, improved, **kw):
    row = {"skill": skill, "command": f"/{skill}", "metric": kw.get("metric", "M"),
           "mechanism_script": kw.get("mech", "x.py"), "diagnosis": kw.get("diag", "d"),
           "status": kw.get("status", "done"), "improved": improved}
    return row


def test_empty_corpus_yields_no_candidates():
    assert sis.scan([], min_cases=5, max_rate=0.5) == []


def test_below_min_cases_is_not_a_candidate():
    rows = [_case("wiki", False) for _ in range(4)]  # 4 < 5
    assert sis.scan(rows, min_cases=5, max_rate=0.5) == []


def test_low_rate_skill_is_flagged_with_failing_cases():
    # 5 cases, 1 improved → 20% ≤ 50% → candidate; carries the 4 failing diagnoses.
    rows = [_case("wiki", True)] + [_case("wiki", False, diag=f"orphan-{i}") for i in range(4)]
    out = sis.scan(rows, min_cases=5, max_rate=0.5)
    assert len(out) == 1
    c = out[0]
    assert c["skill"] == "wiki"
    assert c["cases"] == 5 and c["improved"] == 1
    assert c["success_rate"] == 0.2
    assert len(c["failing_cases"]) == 4
    assert all(fc["diagnosis"].startswith("orphan-") for fc in c["failing_cases"])


def test_high_rate_skill_is_not_flagged():
    rows = [_case("simplify", True) for _ in range(4)] + [_case("simplify", False)]  # 80%
    assert sis.scan(rows, min_cases=5, max_rate=0.5) == []


def test_candidates_sorted_worst_first():
    rows = (
        [_case("a", True)] + [_case("a", False) for _ in range(4)]          # 20%
        + [_case("b", False) for _ in range(5)]                            # 0%
    )
    out = sis.scan(rows, min_cases=5, max_rate=0.5)
    assert [c["skill"] for c in out] == ["b", "a"]  # 0% before 20%


def test_failing_cases_capped():
    rows = [_case("wiki", False, diag=f"d{i}") for i in range(20)]
    out = sis.scan(rows, min_cases=5, max_rate=0.5)
    assert out[0]["cases"] == 20
    assert len(out[0]["failing_cases"]) == sis._MAX_FAILING_CASES


def test_read_corpus_skips_malformed_lines(tmp_path):
    p = tmp_path / "eval_corpus.jsonl"
    p.write_text('{"skill":"wiki","improved":false}\nnot json\n{"skill":"wiki","improved":true}\n',
                 encoding="utf-8")
    rows = sis._read_corpus(p)
    assert len(rows) == 2  # the "not json" line skipped, the rest kept


def test_read_corpus_missing_file_is_empty(tmp_path):
    assert sis._read_corpus(tmp_path / "nope.jsonl") == []
