from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

import pytest

from agentica_core import aggregate as agg


# Requirement: collect() keeps provider usage and retrieval diagnostics separated by
# platform, applies a half-open window, and exposes the current Cache_Hit_Rate and nested
# embedding/search diagnostic contract without leaking source content.


def _require_knowledge_metrics():
    try:
        from agentica_core import knowledge_metrics as km
    except Exception as exc:
        pytest.fail("knowledge metric collector is not implemented: " + str(exc))
    return km


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")


def _diagnostics(status: str) -> dict:
    embedding = {"hit": "hit", "no_hits": "miss", "unavailable": "error",
                 "invalid_response": "error"}.get(status)
    search = {"hit": "ok", "no_hits": "empty", "unavailable": "unavailable",
              "invalid_response": "invalid_response"}.get(status)
    result = {}
    if embedding is not None:
        result["embedding"] = {"result": embedding}
    if search is not None:
        result["search_diagnostics"] = {"wiki_knowledge": {"status": search}}
    return result


def _codex_row(ts: datetime, event_name: str = "UserPromptSubmit", status: str = "hit", **extra):
    row = {
        "at": ts.timestamp(),
        "harness": "codex",
        "event": event_name,
        "sources": [],
        **_diagnostics(status),
    }
    row.update(extra)
    return row


def _qdrant_row(ts: datetime, status: str, *, elapsed_ms: int, context_chars: int | None = None,
                per_collection=None):
    row = {
        "ts": ts.isoformat(),
        "elapsed_ms": elapsed_ms,
        "prompt_len": 120,
        "targets": ["wiki_knowledge", "claude_lessons"],
        **_diagnostics(status),
    }
    if context_chars is not None:
        row["context_chars"] = context_chars
    if per_collection is not None:
        row["search_diagnostics"] = per_collection
    return row

def _native_records_factory(home: Path, platform: str) -> list[dict]:
    base = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    if platform == "claude":
        return [
            {
                "platform": "claude",
                "session_id": "c1",
                "timestamp": (base + timedelta(minutes=2)).isoformat(),
                "_usage_events": [
                    {
                        "timestamp": (base + timedelta(minutes=2)).isoformat(),
                        "model": "gpt-5",
                        "tokens_prompt": 100,
                        "cache_read_tokens": 50,
                        "cache_creation_tokens": 50,
                        "usage_known": True,
                    }
                ],
            }
        ]
    if platform == "codex":
        return [
            {
                "platform": "codex",
                "session_id": "x1",
                "timestamp": (base + timedelta(minutes=3)).isoformat(),
                "_usage_events": [
                    {
                        "timestamp": (base + timedelta(minutes=3)).isoformat(),
                        "model": "gpt-5",
                        "tokens_prompt": 100,
                        "cache_read_tokens": 0,
                        "cache_creation_tokens": 100,
                        "usage_known": True,
                    }
                ],
            }
        ]
    return []


def test_knowledge_metric_collector_import_contract():
    _require_knowledge_metrics()


def test_collect_builds_cache_rate_from_native_records(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    start = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc)
    monkeypatch_records = {
        "claude": _native_records_factory(home, "claude"),
        "codex": _native_records_factory(home, "codex"),
    }

    def fake_native_records(_home, platform):
        return monkeypatch_records[platform]

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", fake_native_records)

    # Combined cached-input share: (50 + 0) / (100 + 100) = 25%.
    result = km.collect(repo=repo, home=home, start=start, end=end, platforms=["claude", "codex"])
    by_platform = result["by_platform"]
    assert "claude" in by_platform
    assert "codex" in by_platform
    assert result["combined"]["Cache_Hit_Rate"]["val"] == pytest.approx(25.0, abs=0.1)
    assert by_platform["claude"]["Cache_Hit_Rate"]["val"] == 50.0
    assert by_platform["codex"]["Cache_Hit_Rate"]["val"] == 0.0
    monkeypatcher.undo()


def test_collect_half_open_timestamp_window_and_userprompt_filtering(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"

    repo_path = repo / "Governance" / "data" / "knowledge_bridge"
    events_path = repo_path / "events.jsonl"
    events = [
        _codex_row(datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc), event_name="UserPromptSubmit"),
        _codex_row(datetime(2026, 9, 13, 12, 10, tzinfo=timezone.utc), event_name="SessionStart"),
        _codex_row(datetime(2026, 9, 13, 12, 29, tzinfo=timezone.utc), event_name="UserPromptSubmit"),
        _codex_row(datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc), event_name="UserPromptSubmit"),
    ]
    _write_jsonl(events_path, events)

    _write_jsonl(
        home / ".claude" / "data" / "qdrant_hit_rate.jsonl.1",
        [_qdrant_row(datetime(2026, 9, 13, 11, 55, tzinfo=timezone.utc), "no_hits", elapsed_ms=20, context_chars=10)],
    )

    start = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc)

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home, start=start, end=end, platforms=["codex"])
    obs = result["combined"]["Embedding_Cache_Hits"]["detail"]
    assert "Data gap" not in obs
    # Start-inclusive, end-exclusive: two UserPromptSubmit rows are accepted
    codex_by_platform = result["by_platform"]["codex"]
    assert codex_by_platform["Retrieval_Observations"]["val"] == 2
    assert codex_by_platform["Embedding_Cache_Hits"]["val"] == 2
    monkeypatcher.undo()


def test_collect_rejects_malformed_lines_and_nonfinite_values(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    qdrant_path = home / ".claude" / "data" / "qdrant_hit_rate.jsonl"

    rows = [
        {"ts": "not-a-timestamp", "elapsed_ms": "fast", **_diagnostics("hit")},
        {"ts": "2026-09-13T12:05:00+00:00", "elapsed_ms": float("nan"), **_diagnostics("hit")},
        {"ts": "2026-09-13T12:06:00+00:00", "elapsed_ms": -5, **_diagnostics("hit")},
        {"ts": "2026-09-13T12:07:00+00:00", "elapsed_ms": 10, "context_chars": True, **_diagnostics("hit")},
    ]
    _write_jsonl(qdrant_path, rows)

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home,
                        start=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc),
                        platforms=["claude"])

    assert result["combined"]["Retrieval_Observations"]["val"] == 3
    assert result["combined"]["Retrieval_Latency_P50_ms"]["val"] == 10
    assert result["combined"]["Retrieval_Latency_P95_ms"]["val"] == result["combined"]["Retrieval_Latency_P50_ms"]["val"]
    monkeypatcher.undo()


def test_collect_rotated_qdrant_file_is_included(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    base = home / ".claude" / "data"
    _write_jsonl(base / "qdrant_hit_rate.jsonl", [
        _qdrant_row(datetime(2026, 9, 13, 12, 1, tzinfo=timezone.utc), "hit", elapsed_ms=50, context_chars=120),
    ])
    _write_jsonl(base / "qdrant_hit_rate.jsonl.1", [
        _qdrant_row(datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc), "no_hits", elapsed_ms=30, context_chars=80),
    ])

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home,
                        start=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 9, 13, 12, 30, tzinfo=timezone.utc),
                        platforms=["claude"])

    assert result["combined"]["Embedding_Cache_Hits"]["val"] == 1
    assert result["combined"]["Embedding_Cache_Misses"]["val"] == 1
    monkeypatcher.undo()


def test_collect_retrieval_failures_only_count_unavailable_and_invalid_response(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    base = home / ".claude" / "data"
    _write_jsonl(base / "qdrant_hit_rate.jsonl", [
        _qdrant_row(datetime(2026, 9, 13, 12, 2, tzinfo=timezone.utc),
                     "hit", elapsed_ms=20, context_chars=90),
        _qdrant_row(datetime(2026, 9, 13, 12, 4, tzinfo=timezone.utc),
                     "no_hits", elapsed_ms=10, context_chars=45),
        _qdrant_row(datetime(2026, 9, 13, 12, 6, tzinfo=timezone.utc),
                     "unavailable", elapsed_ms=60, context_chars=80),
        _qdrant_row(datetime(2026, 9, 13, 12, 7, tzinfo=timezone.utc),
                     "invalid_response", elapsed_ms=40, context_chars=20),
    ])

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home,
                        start=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc),
                        platforms=["claude"])

    assert result["combined"]["Retrieval_Search_Failures"]["val"] == 2
    assert result["combined"]["Embedding_Cache_Hits"]["val"] == 1
    assert result["combined"]["Embedding_Cache_Misses"]["val"] == 1
    monkeypatcher.undo()


def test_collect_injected_context_percentiles_are_sample_based(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    base = home / ".claude" / "data"

    _write_jsonl(base / "qdrant_hit_rate.jsonl", [
        _qdrant_row(datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc), "hit", elapsed_ms=10),
        _qdrant_row(datetime(2026, 9, 13, 12, 1, tzinfo=timezone.utc), "hit", elapsed_ms=30, context_chars=50),
        _qdrant_row(datetime(2026, 9, 13, 12, 2, tzinfo=timezone.utc), "hit", elapsed_ms=50, context_chars=200),
    ])

    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home,
                        start=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc),
                        platforms=["claude"])

    p50 = result["combined"]["Injected_Context_Chars_P50"]["val"]
    p95 = result["combined"]["Injected_Context_P95_ms"]["val"] if "Injected_Context_P95_ms" in result["combined"] else result["combined"]["Retrieval_Latency_P95_ms"]["val"]
    assert p50 == 125.0
    assert result["combined"]["Retrieval_Observations"]["val"] == 3
    assert p95 == 48.0
    monkeypatcher.undo()


def test_collect_does_not_leak_prompt_or_source_text_into_details(tmp_path):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    base = home / ".claude" / "data"
    _write_jsonl(base / "qdrant_hit_rate.jsonl", [
        {
            "ts": "2026-09-13T12:00:00+00:00",
            "embedding": {"result": "hit"},
            "elapsed_ms": 10,
            "context_chars": 10,
            "search_diagnostics": {"wiki_knowledge": {"status": "ok"}},
            "sources": ["/tmp/secret.md"],
            "context": "should not appear"
        }
    ])
    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setattr(km, "_native_records", lambda _home, platform: [])
    result = km.collect(repo=repo, home=home,
                        start=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 9, 13, 13, 0, tzinfo=timezone.utc),
                        platforms=["claude"])

    assert result["combined"]["Retrieval_Observations"]["val"] == 1
    detail = result["combined"]["Retrieval_Search_Failures"].get("detail", "")
    assert "should not appear" not in json.dumps(detail)
    assert "/tmp/secret.md" not in json.dumps(detail)
    monkeypatcher.undo()


def test_aggregate_wires_collect_into_build_pillars_and_limits_scope(tmp_path, monkeypatch):
    km = _require_knowledge_metrics()
    repo = tmp_path / "repo"
    home = tmp_path / "home"

    seen = {"collect_calls": 0, "build_pillars_calls": 0}

    def fake_collect(repo_path, home_path, start, end, platforms=None):
        seen["collect_calls"] += 1
        assert isinstance(repo_path, Path)
        assert isinstance(home_path, Path)
        assert platforms in (["claude"], ["fake"], None) or platforms is None
        return {
            "combined": {
                "Cache_Hit_Rate": {"val": 12.5, "calibrated": True},
            },
            "by_platform": {
                "claude": {"Cache_Hit_Rate": {"val": 25.0, "calibrated": True}},
            },
        }

    def fake_build_pillars(records, **kwargs):
        seen["build_pillars_calls"] += 1
        # Measurement signals should be injected where supported; project/tier scopes are not here.
        return {name: {} for name in agg.PILLARS}

    def fake_insights(*args, **kwargs):
        return {name: {} for name in agg.PILLARS}

    monkeypatch.setattr(agg, "load_records", lambda _platform: [])
    from types import SimpleNamespace
    monkeypatch.setattr(agg, "resolve_platform", lambda p: SimpleNamespace(runtime_root=tmp_path))
    monkeypatch.setattr(agg, "run_all", lambda verifiers: [])
    monkeypatch.setattr(agg, "load_verifiers", lambda p: [])
    monkeypatch.setattr(agg, "build_pillars", fake_build_pillars)
    monkeypatch.setattr(agg, "build_project_scores", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agg.scouts, "agent_process_count", lambda: 0)
    monkeypatch.setattr(agg.scouts, "security_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agg.scouts, "knowledge_signals", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agg.verify_secrets, "run_checks", lambda: [])
    monkeypatch.setattr(agg, "load_git_records", lambda: [])
    monkeypatch.setattr(agg.insights, "annotate", fake_insights)
    monkeypatch.setattr(agg.insights, "populate_history", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agg.insights, "build_summaries", lambda *_args, **_kwargs: {name: {} for name in agg.PILLARS})
    monkeypatch.setattr(agg.reflexes, "build_reflexes", lambda *_args, **_kwargs: ({}, {}))
    monkeypatch.setattr(agg.remediation, "efficacy", lambda **kwargs: {})
    monkeypatch.setattr(agg, "_top_usage", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agg.operator_attention, "build_safe", lambda **kwargs: {})
    monkeypatch.setattr(agg, "architecture_breakdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agg, "knowledge_metrics", km)
    monkeypatch.setattr(km, "collect", fake_collect)

    payload = agg.aggregate(platforms=["claude"], timestamp="2026-09-13T13:00:00+00:00", window_days=7)
    assert seen["collect_calls"] == 1
    assert seen["build_pillars_calls"] >= 2
    assert "platforms" in payload
    assert payload["platforms"] == ["claude"]
