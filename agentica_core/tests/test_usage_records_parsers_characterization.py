"""Characterization of usage_records._codex(), _claude() and enrich() before the W3 refactor.

Plan: docs/plans/2026-10-07-order-samurai-hotspot-split/PLAN.md, W3 ("no other function in the
file above C" pulls these E-grade functions in: _codex 40, enrich 35, _claude 33). Fixture
transcripts exercise every branch; outputs are compared with
fixtures/usage_records_parsers_snapshots.json, recorded from the pre-refactor code
(origin/work @ f6e3a6859). Never regenerate it to make a refactor pass. model_tier is pinned
and the _parse lru_cache is cleared around each case.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentica_core import usage_records as ur

SNAPSHOTS = Path(__file__).with_name("fixtures") / "usage_records_parsers_snapshots.json"
TIERS = {"opus": "PREMIUM", "sonnet": "STANDARD", "gpt-6": "PREMIUM"}


def jsonl(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def codex_rows() -> list:
    tc = lambda stamp, inp, out, cached=0, write=0, last=None, extra=None: {  # noqa: E731
        "type": "event_msg", "timestamp": stamp, "payload": {"type": "token_count", "info": {
            "total_token_usage": dict({"input_tokens": inp, "output_tokens": out,
                                       "cached_input_tokens": cached,
                                       "cache_write_input_tokens": write}, **(extra or {})),
            "last_token_usage": {"input_tokens": last if last is not None else inp}}}}
    return [
        {"type": "event_msg", "timestamp": "2026-09-01T09:59:00Z",           # before session start
         "payload": {"type": "user_message", "message": "inherited"}},
        {"type": "session_meta", "timestamp": "2026-09-01T10:00:00Z",
         "payload": {"id": "codex-sess", "cwd": "/work/order-samurai", "timestamp": "2026-09-01T10:00:00Z"}},
        {"type": "turn_context", "timestamp": "2026-09-01T10:00:01Z",
         "payload": {"model": "gpt-6", "turn_id": "t1"}},
        {"type": "event_msg", "timestamp": "2026-09-01T09:58:00Z",           # replayed history
         "payload": {"type": "agent_message", "message": "inherited reply"}},
        {"type": "event_msg", "timestamp": "2026-09-01T10:00:02Z",
         "payload": {"type": "user_message", "message": "hi"}},
        tc("2026-09-01T10:00:03Z", 100, 10, cached=40, write=5),
        tc("2026-09-01T10:00:04Z", 100, 10, cached=40, write=5),           # repeat: no growth
        tc("2026-09-01T10:00:05Z", 160, 25, cached=200, write=5),          # cached > input delta
        {"type": "event_msg", "timestamp": "2026-09-01T10:00:06Z",
         "payload": {"type": "token_count", "info": {}}},                   # no totals
        tc("2026-09-01T10:00:07Z", 30, 2),                                  # counter reset
        tc("2026-09-01T10:00:08Z", 50, 4, extra={"input_tokens": "x"}),     # unknown count
        {"type": "response_item", "timestamp": "2026-09-01T10:00:09Z",
         "payload": {"type": "function_call", "call_id": "c1", "name": "shell",
                     "arguments": json.dumps({"cmd": "ls"})}},
        {"type": "response_item", "timestamp": "2026-09-01T10:00:10Z",
         "payload": {"type": "custom_tool_call", "id": "c2", "name": "spawn_agent",
                     "arguments": {"agent_type": "  reviewer  "}}},
        {"type": "response_item", "timestamp": "2026-09-01T10:00:11Z",
         "payload": {"type": "function_call", "name": "multi.Skill", "arguments": "{bad json"}},
        {"type": "event_msg", "timestamp": "2026-09-01T10:00:12Z",
         "payload": {"type": "agent_message", "message": "three words here"}},
        {"type": "event_msg", "timestamp": "2026-09-01T10:00:13Z", "payload": {"type": "task_complete"}},
        {"type": "event_msg", "timestamp": "2026-09-01T10:00:14Z", "payload": {"type": "other"}},
        "not json", "[1]",
    ]


def claude_rows() -> list:
    usage = lambda inp, out, cr, cc, split=None: dict(  # noqa: E731
        {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
         "cache_creation_input_tokens": cc}, **({"cache_creation": split} if split is not None else {}))
    return [
        {"type": "user", "cwd": "/work/repo", "uuid": "u1", "timestamp": "2026-09-01T11:00:00Z",
         "message": {"role": "user", "content": "please fix"}},
        {"type": "user", "uuid": "u2", "timestamp": "2026-09-01T11:00:01Z",
         "message": {"content": "<system-reminder>ignored</system-reminder>"}},
        {"type": "user", "timestamp": "2026-09-01T11:00:02Z", "message": {"content": [{"type": "tool_result"}]}},
        {"type": "user", "timestamp": "2026-09-01T11:00:03Z", "message": "not-a-dict"},
        {"type": "assistant", "uuid": "a1", "timestamp": "2026-09-01T11:00:04Z",
         "message": {"id": "req1", "model": "opus", "usage": usage(10, 5, 100, 20, {"ephemeral_5m_input_tokens": 20}),
                     "content": [{"type": "text", "text": "two words"}, {"type": "thinking"},
                                 {"type": "tool_use", "id": "tu1", "name": "Task",
                                  "input": {"subagent_type": "Explore"}},
                                 {"type": "tool_use", "name": "Skill", "input": {"skill": "verify"}},
                                 "junk"]}},
        {"type": "assistant", "uuid": "a2", "timestamp": "2026-09-01T11:00:05Z",          # stream repeat
         "message": {"id": "req1", "model": "opus", "usage": usage(12, 4, 90, 25, {"ephemeral_1h_input_tokens": 7}),
                     "content": "not-a-list"}},
        {"type": "assistant", "uuid": "a3", "timestamp": "2026-09-01T11:00:06Z",
         "message": {"id": "req2", "model": "sonnet", "usage": usage(3, 1, "?", 0, "bad"), "content": []}},
        {"type": "assistant", "timestamp": "2026-09-01T11:00:07Z",
         "message": {"model": "<synthetic>", "usage": usage(1, 1, 0, 0)}},
        {"type": "assistant", "requestId": "req3", "timestamp": "2026-09-01T11:00:08Z",
         "message": {"model": "sonnet", "content": [{"type": "text", "text": None}]}},
    ]


def run_parser(name: str, tmp_path: Path) -> list:
    ur._parse.cache_clear()
    if name == "codex":
        out = ur._codex(jsonl(tmp_path / "rollout-1.jsonl", codex_rows()))
    else:
        out = ur._claude(jsonl(tmp_path / "s1.jsonl", claude_rows()))
    return json.loads(json.dumps(out, sort_keys=True))


def run_enrich(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list:
    ur._parse.cache_clear()
    monkeypatch.setattr(ur, "model_tier", lambda m: TIERS.get(m or "", "UNKNOWN"))
    runtime = tmp_path / "runtime"
    if name == "claude_projects":
        proj = runtime / "projects"
        jsonl(proj / "repo" / "s1.jsonl", claude_rows())
        jsonl(proj / "repo" / "copies" / "nested" / "s1.jsonl", claude_rows())   # copy: deduped
        jsonl(proj / "repo" / "empty.jsonl", [{"type": "user", "message": {"content": ""}}])
        jsonl(proj / "other" / "s1.jsonl", [                                     # same session, new work
            {"type": "assistant", "uuid": "b1", "timestamp": "2026-09-01T13:00:00Z",
             "message": {"id": "req9", "model": "sonnet",
                         "usage": {"input_tokens": 7, "output_tokens": 2,
                                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}])
        raw = [
            {"session_id": "s1", "timestamp": "2026-09-01T10:00:00Z", "project": "", "tokens_prompt": 1,
             "slop_markers": 3, "total_cost": 9.0, "platform": "claude"},
            {"session_id": "s1", "timestamp": "2026-09-01T12:00:00Z", "project": "kept-project", "turns": 4},
            {"session_id": "gone", "timestamp": "2026-09-01T12:00:00Z", "tokens_prompt": 5},
            {"session_id": "gone-zero", "timestamp": "2026-09-01T12:00:00Z", "tokens_prompt": 0},
            {"timestamp": "2026-09-01T12:00:00Z", "tokens_prompt": 5},
        ]
        out = ur.enrich("claude", raw, runtime)
    elif name == "codex_sessions":
        # Three files of one session, shortest path first: incomplete, then complete (upgrades the
        # status), then incomplete again (leaves it).
        jsonl(runtime / "sessions" / "2026" / "rollout-1.jsonl",
              [r for r in codex_rows() if not (isinstance(r, dict)
                                                and r["payload"].get("type") == "task_complete")])
        jsonl(runtime / "sessions" / "2026" / "10" / "rollout-2.jsonl", [
            {"type": "session_meta", "timestamp": "2026-09-01T20:00:00Z",
             "payload": {"id": "codex-sess", "cwd": "/work/order-samurai", "timestamp": "2026-09-01T20:00:00Z"}},
            {"type": "event_msg", "timestamp": "2026-09-01T20:00:01Z", "payload": {"type": "user_message", "message": "go"}},
            {"type": "event_msg", "timestamp": "2026-09-01T20:00:02Z", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 4, "output_tokens": 1, "cached_input_tokens": 0,
                                      "cache_write_input_tokens": 0}}}},
            {"type": "event_msg", "timestamp": "2026-09-01T20:00:03Z", "payload": {"type": "task_complete"}}])
        jsonl(runtime / "sessions" / "2026" / "10" / "11" / "rollout-3.jsonl", [   # same id, no task_complete
            {"type": "session_meta", "timestamp": "2026-09-02T08:00:00Z",
             "payload": {"id": "codex-sess", "cwd": "/work/order-samurai", "timestamp": "2026-09-02T08:00:00Z"}},
            {"type": "turn_context", "timestamp": "2026-09-02T08:00:01Z", "payload": {"model": "gpt-6", "turn_id": "t9"}},
            {"type": "event_msg", "timestamp": "2026-09-02T08:00:02Z", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 9, "output_tokens": 3, "cached_input_tokens": 0,
                                      "cache_write_input_tokens": 0}}}}])
        out = ur.enrich("codex", [{"session_id": "codex-sess", "timestamp": "2026-09-01T10:00:00Z"}], runtime)
    elif name == "explicit_source":
        src = tmp_path / "elsewhere"
        jsonl(src / "s1.jsonl", claude_rows())
        out = ur.enrich("claude", [], runtime, source=src)
    else:  # non-native platform passes through
        raw = [{"session_id": "g", "timestamp": "2026-09-01T10:00:00Z"}]
        out = ur.enrich("gemini", raw, runtime)
        assert out is raw
    return json.loads(json.dumps(out, sort_keys=True, default=str))


PARSER_CASES = ("codex", "claude")
ENRICH_CASES = ("claude_projects", "codex_sessions", "explicit_source", "non_native_platform")


@pytest.mark.parametrize("name", PARSER_CASES)
def test_parser_matches_the_pre_refactor_snapshot(name, tmp_path):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[f"parser:{name}"]
    assert run_parser(name, tmp_path) == expected


@pytest.mark.parametrize("name", ENRICH_CASES)
def test_enrich_matches_the_pre_refactor_snapshot(name, tmp_path, monkeypatch):
    expected = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))[f"enrich:{name}"]
    assert run_enrich(name, tmp_path, monkeypatch) == expected


def test_every_case_has_a_recorded_snapshot():
    keys = {f"parser:{n}" for n in PARSER_CASES} | {f"enrich:{n}" for n in ENRICH_CASES}
    assert set(json.loads(SNAPSHOTS.read_text(encoding="utf-8"))) == keys


def test_unreadable_transcript_is_skipped(tmp_path, monkeypatch):
    """A path that vanishes between rglob and stat is skipped, not raised."""
    ur._parse.cache_clear()
    monkeypatch.setattr(ur, "model_tier", lambda m: "STANDARD")
    proj = tmp_path / "runtime" / "projects"
    jsonl(proj / "s1.jsonl", claude_rows())
    (proj / "dangling.jsonl").symlink_to(proj / "missing-target.jsonl")
    out = ur.enrich("claude", [], tmp_path / "runtime")
    assert [r["session_id"] for r in out] == ["s1"]
