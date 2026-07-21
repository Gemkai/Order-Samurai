from agentica_core import insights, threshold_audit


def _env(status=None, weight=1.0, **extra):
    e = {"val": "1", "is_simulated": False, **extra}
    if status:
        e["status"] = status
        e["rule"] = {"dir": "lower", "warn": 1, "fail": 5, "weight": weight, "per": None}
    return e


def test_needs_attention_counts_and_sorts():
    pillars = {
        "bow": {"G": {"Ok_Metric": _env("OK"), "Warn_Metric": _env("WARN", weight=1.0)}},
        "sword": {"G": {"Fail_Light": _env("FAIL", weight=1.0), "Fail_Heavy": _env("FAIL", weight=3.0)}},
        "brush": {"G": {"Info_Metric": _env()}},   # no status → informational, excluded
        "arts": {"G": {"Sim_Metric": _env("WARN", is_simulated=True)}},  # simulated still has status? excluded below
    }
    na = insights.needs_attention(pillars)
    # WARN + 2 FAIL = 3 (OK and informational excluded). Simulated WARN: status present so it
    # would count — but annotate never sets status on simulated metrics, so this guards the
    # contract that only annotate-set statuses reach here.
    keys = [i["metric"] for i in na["items"]]
    assert "Ok_Metric" not in keys and "Info_Metric" not in keys
    # FAIL before WARN; within FAIL, heavier weight first (sort hint).
    assert keys[:2] == ["Fail_Heavy", "Fail_Light"]
    assert na["items"][0]["status"] == "FAIL"
    assert na["count"] == len(na["items"])


def test_needs_attention_empty_all_clear():
    pillars = {"bow": {"G": {"A": _env("OK"), "B": _env("OK")}}}
    na = insights.needs_attention(pillars)
    assert na == {"count": 0, "items": []}


def test_count_matches_breaching_metrics_end_to_end():
    """The count equals exactly the number of WARN+FAIL envelopes after real annotate()."""
    # Error_Rate warn 2 / fail 5, dir lower. value 9 → past fail → FAIL.
    pillars = {
        "bow": {"Activity": {"Error_Rate": {"val": "9", "is_simulated": False}}},
        "sword": {}, "brush": {}, "arts": {},
    }
    insights.annotate(pillars)
    na = insights.needs_attention(pillars)
    breaching = sum(1 for g in pillars.values() for ms in g.values()
                    for e in ms.values() if e.get("status") in ("WARN", "FAIL"))
    assert na["count"] == breaching == 1
    assert na["items"][0]["metric"] == "Error_Rate"


def test_threshold_audit_captures_edit(tmp_path):
    audit = tmp_path / "threshold_audit.jsonl"
    snap = tmp_path / "threshold_snapshot.json"
    rules = {"Error_Rate": {"dir": "lower", "warn": 2, "fail": 5}}

    # First run seeds the baseline silently — no change recorded.
    assert threshold_audit.audit_threshold_changes(rules, audit_path=audit, snapshot_path=snap, now="t0") == []
    assert not audit.exists()

    # Loosen the fail threshold → must be captured.
    loosened = {"Error_Rate": {"dir": "lower", "warn": 2, "fail": 20}}
    changes = threshold_audit.audit_threshold_changes(loosened, audit_path=audit, snapshot_path=snap, now="t1")
    assert len(changes) == 1
    c = changes[0]
    assert c["metric"] == "Error_Rate" and c["change"] == "threshold"
    assert c["old"]["fail"] == 5 and c["new"]["fail"] == 20
    assert audit.read_text(encoding="utf-8").strip()  # line written


def test_threshold_audit_records_added_and_removed(tmp_path):
    audit = tmp_path / "a.jsonl"
    snap = tmp_path / "s.json"
    threshold_audit.audit_threshold_changes({"A": {"dir": "lower", "warn": 1, "fail": 2}},
                                            audit_path=audit, snapshot_path=snap, now="t0")
    changes = threshold_audit.audit_threshold_changes(
        {"B": {"dir": "higher", "warn": 9, "fail": 5}},  # A removed, B added
        audit_path=audit, snapshot_path=snap, now="t1")
    kinds = {(c["metric"], c["change"]) for c in changes}
    assert ("A", "removed") in kinds and ("B", "added") in kinds
