"""Fixture builders for the harness eval suite.

Each builder materialises a synthetic-but-format-faithful workspace into a directory the runner
owns, and returns the list of seeded file paths for fingerprinting. Transcript fixtures round-trip
through the REAL parser (`agentica_core.evals.transcript_source._iter_file` expectations): one
JSON object per line, assistant lines carrying `message.content` blocks, tool results arriving on
later user lines keyed by `tool_use_id`, usage ints under `message.usage`.

Determinism rules:
- mtimes are spaced explicitly with os.utime — "depth" (Nth-newest) is the load-bearing property
  for the bounded-scan tasks, and filesystem enumeration order must never decide it.
- No randomness anywhere. A fixture that differs between repeats would turn a deterministic
  grader into a coin flip and the acceptance rule into noise.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _assistant_tool_use(tid: str, name: str, args: dict) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [
            {"type": "text", "text": f"calling {name}"},
            {"type": "tool_use", "id": tid, "name": name, "input": args},
        ]},
    }


def _user_tool_result(tid: str, text: str) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]},
    }


def _clean_session(file_index: int) -> list[dict]:
    """One trusted tool call, args unique per file so no cross-file pattern exists."""
    tid = f"clean_{file_index}"
    return [
        {"type": "user", "message": {"content": f"task {file_index}"}},
        _assistant_tool_use(tid, "Read", {"file_path": f"src/file_{file_index}.py"}),
        _user_tool_result(tid, "file contents"),
    ]


def _retry_session() -> list[dict]:
    """The same Read issued twice with identical args — an unambiguous identical retry."""
    return [
        {"type": "user", "message": {"content": "find the setting"}},
        _assistant_tool_use("r1", "Read", {"file_path": "config/settings.json"}),
        _user_tool_result("r1", "truncated garbage"),
        _assistant_tool_use("r2", "Read", {"file_path": "config/settings.json"}),
        _user_tool_result("r2", "truncated garbage"),
    ]


def _errored_session() -> list[dict]:
    """A call that errors and is never re-run — labelled errored_no_retry by the annotator."""
    return [
        {"type": "user", "message": {"content": "run the build"}},
        _assistant_tool_use("e1", "Bash", {"command": "make build"}),
        _user_tool_result("e1", "Error: command not found: make"),
    ]


def _projects_dir(
    workspace: Path, *, total_files: int, marker_depth: int | None, marker_rows: list[dict] | None
) -> list[Path]:
    """A synthetic ~/.claude/projects tree of `total_files` transcripts with controlled recency.

    Depth d = the d-th newest file (depth 1 = newest). The marker session, when given, is placed
    at exactly `marker_depth`; every other file is a clean session. mtimes descend one minute per
    depth step from a fixed base, so the "newest N files" window is exact and reproducible.
    """
    if (marker_depth is None) != (marker_rows is None):
        raise ValueError("marker_depth and marker_rows must be given together")
    proj = workspace / "projects" / "synthetic-project"
    base = time.time()
    seeded: list[Path] = []
    for depth in range(1, total_files + 1):
        use_marker = marker_depth is not None and depth == marker_depth
        rows = marker_rows if (use_marker and marker_rows is not None) else _clean_session(depth)
        p = _write_jsonl(proj / f"session_{depth:03d}.jsonl", rows)
        mtime = base - depth * 60
        os.utime(p, (mtime, mtime))
        seeded.append(p)
    return seeded


# --- Group A: bounded transcript scan (knob: scout_max_files) --------------------------------

def a1_recent_retry(workspace: Path) -> list[Path]:
    return _projects_dir(workspace, total_files=10, marker_depth=1, marker_rows=_retry_session())


def a2_deep_retry(workspace: Path) -> list[Path]:
    """The marker sits at depth 75 — past today's 60-file window. The video's clamp failure:
    the signal exists, the bounded scan just never reaches it."""
    return _projects_dir(workspace, total_files=80, marker_depth=75, marker_rows=_retry_session())


def a3_all_clean(workspace: Path) -> list[Path]:
    return _projects_dir(workspace, total_files=10, marker_depth=None, marker_rows=None)


def a4_deep_errored(workspace: Path) -> list[Path]:
    """Held-out sibling of a2: same root cause (bounded scan), different symptom and depth."""
    return _projects_dir(workspace, total_files=80, marker_depth=70, marker_rows=_errored_session())


# --- Group B: judge pipeline (knob: judge_max_tokens) -----------------------------------------

def b1_judge_fixture(workspace: Path) -> list[Path]:
    """The judged material as a seeded file, so tampering with it is detectable like any seed."""
    p = workspace / "judge_input.json"
    p.write_text(json.dumps({
        "input": "What is 2 + 2?",
        "output": "2 + 2 = 4.",
    }), encoding="utf-8")
    return [p]


# --- Group C: cliff reducer (knob: context_cliff_token_threshold) -----------------------------

def _usage_session(max_ctx: int) -> list[dict]:
    return [{
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "working"}],
            "usage": {"input_tokens": max_ctx, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
        },
    }]


def _cliff_tree(workspace: Path, ctx_values: list[int]) -> list[Path]:
    """A fake home dir: USERPROFILE points here, so the reducer scans .claude/projects below it."""
    proj = workspace / ".claude" / "projects" / "synthetic"
    seeded = []
    base = time.time()
    for i, ctx in enumerate(ctx_values):
        p = _write_jsonl(proj / f"cliff_{i}.jsonl", _usage_session(ctx))
        os.utime(p, (base - i, base - i))
        seeded.append(p)
    return seeded


def c1_mixed_sessions(workspace: Path) -> list[Path]:
    """3 heavy (150k/200k/300k) + 2 healthy (90k/100k). Exactly 3 cliffs at threshold 140k;
    raising the threshold past 150k or dropping it below 100k both change the count."""
    return _cliff_tree(workspace, [200_000, 300_000, 150_000, 90_000, 100_000])


def c2_healthy_sessions(workspace: Path) -> list[Path]:
    """Held-out: all sessions in the healthy 100–120k band. Any threshold drop below 100k starts
    flagging healthy sessions as cliffs."""
    return _cliff_tree(workspace, [100_000, 110_000, 120_000])


# --- Group D: loop-breaker replay (no files — scenario lives in the grader) -------------------

def d_no_fixture(workspace: Path) -> list[Path]:  # noqa: ARG001 — uniform builder signature
    return []
