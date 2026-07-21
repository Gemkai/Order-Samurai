"""Tests for scouts/kill_chain_discovery.py — taxonomy-vs-telemetry correlation.

Covers the recency window parsing, the confidence>=0.5 prompt-injection filter
(the guard that keeps 'Clean' noise events from proposing chains), proposal
generation with the confidence ratio, and the merge semantics that preserve
approved/rejected entries while replacing stale proposals.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agentica_core.scouts import kill_chain_discovery as kcd


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


NOW = datetime.now(timezone.utc)
FRESH = _iso(NOW - timedelta(hours=1))
STALE = _iso(NOW - timedelta(days=30))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# ------------------------------------------------------- _recent_events

def test_recent_events_filters_by_window(tmp_path):
    p = tmp_path / "ev.jsonl"
    _write_jsonl(p, [{"ts": FRESH, "id": 1}, {"ts": STALE, "id": 2}])
    got = kcd._recent_events(p, days=7)
    assert [e["id"] for e in got] == [1]


def test_recent_events_skips_comments_garbage_and_untimestamped(tmp_path):
    p = tmp_path / "ev.jsonl"
    p.write_text(
        "# comment\nnot json\n"
        + json.dumps({"id": 1}) + "\n"          # no ts -> skipped
        + json.dumps({"ts": FRESH, "id": 2}) + "\n",
        encoding="utf-8",
    )
    got = kcd._recent_events(p, days=7)
    assert [e["id"] for e in got] == [2]


def test_recent_events_accepts_timestamp_key_and_naive_ts(tmp_path):
    p = tmp_path / "ev.jsonl"
    naive = (NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    _write_jsonl(p, [{"timestamp": naive, "id": 1}])
    got = kcd._recent_events(p, days=7)
    assert [e["id"] for e in got] == [1]


def test_recent_events_missing_file(tmp_path):
    assert kcd._recent_events(tmp_path / "absent.jsonl", days=7) == []


# --------------------------------------------- prompt-injection filter

def test_prompt_injection_check_requires_high_confidence(tmp_path):
    state = tmp_path / "state"
    _write_jsonl(state / "kill_chain_unmatched.jsonl", [
        {"ts": FRESH, "event_type": "prompt_injection", "confidence": 0.2},
        {"ts": FRESH, "event_type": "other", "confidence": 0.9},
    ])
    with patch.object(kcd, "_OS_ROOT", tmp_path):
        assert kcd._check_prompt_injection() is False
    # add one high-confidence injection event -> fires
    _write_jsonl(state / "kill_chain_unmatched.jsonl", [
        {"ts": FRESH, "event_type": "prompt_injection", "confidence": 0.8},
    ])
    with patch.object(kcd, "_OS_ROOT", tmp_path):
        assert kcd._check_prompt_injection() is True


# ------------------------------------------------------------- run()

def _setup_os_root(tmp_path: Path, chains: list[dict], events: list[dict] = (),
                   existing: dict | None = None) -> Path:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "kill_chain_taxonomy.json").write_text(
        json.dumps({"chains": chains}), encoding="utf-8"
    )
    if events:
        _write_jsonl(state / "kill_chain_events.jsonl", events)
    if existing is not None:
        (state / "proposed_kill_chains.json").write_text(
            json.dumps(existing), encoding="utf-8"
        )
    return tmp_path


def test_run_missing_taxonomy_returns_zeros(tmp_path):
    with patch.object(kcd, "_OS_ROOT", tmp_path):
        out = kcd.run()
    assert out == {"kill_chain_candidates": 0, "chains_checked": 0}


def test_run_proposes_untracked_chain_with_firing_signal(tmp_path):
    chains = [
        {"id": "KC1", "name": "exfil", "detection_points": ["secret_scrubber", "security_gate"]},
        {"id": "KC2", "name": "no-dps"},  # no detection points -> skipped
    ]
    root = _setup_os_root(tmp_path, chains)
    with patch.object(kcd, "_OS_ROOT", root), \
         patch.dict(kcd._SIGNAL_CHECKS, {"secret_scrubber": lambda: True,
                                         "security_gate": lambda: False}):
        out = kcd.run()
    assert out == {"kill_chain_candidates": 1, "chains_checked": 2}
    written = json.loads((root / "state" / "proposed_kill_chains.json").read_text())
    (prop,) = written["proposals"]
    assert prop["chain_id"] == "KC1"
    assert prop["status"] == "proposed"
    assert prop["firing_detection_points"] == ["secret_scrubber"]
    assert prop["confidence"] == 0.5  # 1 of 2 detection points firing


def test_run_skips_chains_already_tracked_in_events(tmp_path):
    chains = [{"id": "KC1", "name": "x", "detection_points": ["secret_scrubber"]}]
    events = [{"ts": FRESH, "chain_id": "KC1"}]
    root = _setup_os_root(tmp_path, chains, events=events)
    with patch.object(kcd, "_OS_ROOT", root), \
         patch.dict(kcd._SIGNAL_CHECKS, {"secret_scrubber": lambda: True}):
        out = kcd.run()
    assert out["kill_chain_candidates"] == 0


def test_run_merge_keeps_approved_and_replaces_proposed(tmp_path):
    chains = [{"id": "KC2", "name": "y", "detection_points": ["secret_scrubber"]}]
    existing = {
        "proposals": [
            {"chain_id": "OLD1", "status": "approved"},
            {"chain_id": "OLD2", "status": "proposed"},  # stale -> dropped
        ],
        "last_run": "2026-01-01T00:00:00+00:00",
        "approved_count": 1,
    }
    root = _setup_os_root(tmp_path, chains, existing=existing)
    with patch.object(kcd, "_OS_ROOT", root), \
         patch.dict(kcd._SIGNAL_CHECKS, {"secret_scrubber": lambda: True}):
        kcd.run()
    written = json.loads((root / "state" / "proposed_kill_chains.json").read_text())
    by_id = {p["chain_id"]: p for p in written["proposals"]}
    assert set(by_id) == {"OLD1", "KC2"}          # approved kept, stale proposed dropped
    assert by_id["OLD1"]["status"] == "approved"
    assert written["approved_count"] == 1
    assert written["last_run"] != "2026-01-01T00:00:00+00:00"


def test_run_no_signal_no_proposal(tmp_path):
    chains = [{"id": "KC3", "name": "z", "detection_points": ["security_gate"]}]
    root = _setup_os_root(tmp_path, chains)
    with patch.object(kcd, "_OS_ROOT", root), \
         patch.dict(kcd._SIGNAL_CHECKS, {"security_gate": lambda: False}):
        out = kcd.run()
    assert out["kill_chain_candidates"] == 0
    written = json.loads((root / "state" / "proposed_kill_chains.json").read_text())
    assert written["proposals"] == []
