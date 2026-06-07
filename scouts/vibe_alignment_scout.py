"""Anti-slop vibe alignment scout.

Purpose: score recently modified source files for AI slop density using the local
  gemma-4-e4b model (LM Studio at localhost:1234). Writes state/vibe_alignment.json
  with a 0-100 score for the aggregate to read.

Owner: arts-pillar
Inputs:
  - Recently modified .py/.ts files (top 5 by mtime) in execution/, scouts/, agentica_core/
  - Local LM Studio at http://localhost:1234/v1 (gemma-4-e4b)
Outputs:
  - state/vibe_alignment.json
Failure modes:
  - LM Studio not running: writes score=null, reason="offline" — metric returns 0.0
  - No source files found: writes score=null, reason="no_files"
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[1]
STATE_DIR = REPO_ROOT / "state"
OUTPUT = STATE_DIR / "vibe_alignment.json"

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "google/gemma-4-e4b"
TIMEOUT_S = 30

# Kept short to avoid empty-response from the model on long prompts.
SLOP_PROMPT_SYSTEM = (
    "You are a code quality judge. Rate code for AI slop (0-100, higher=cleaner). "
    'Respond ONLY with JSON: {"score": <int>, "top_issue": "<one sentence>"}. '
    "Slop signals: obvious what-comments, generic names (data/item/result), "
    "duplicate error handling, dead branches, docstrings restating the signature."
)


def _get_recent_files(n: int = 3) -> list[Path]:
    source_dirs = ["execution", "scouts", "agentica_core"]
    source_exts = {".py", ".ts"}
    candidates: list[tuple[float, Path]] = []
    for sdir in source_dirs:
        d = REPO_ROOT / sdir
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix in source_exts and p.is_file() and "__pycache__" not in str(p):
                try:
                    candidates.append((p.stat().st_mtime, p))
                except OSError:
                    pass
    candidates.sort(reverse=True)
    return [p for _, p in candidates[:n]]


def _read_snippet(path: Path, max_lines: int = 30) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        snippet = "\n".join(lines[:max_lines])
        return f"### {path.name} ({len(lines)} lines)\n{snippet}"
    except OSError:
        return ""


def _call_lm_studio(snippets_text: str) -> dict | None:
    # gemma-4-e4b uses internal reasoning tokens before emitting content.
    # max_tokens must be large enough to cover the thinking pass (~300) + output (~50).
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SLOP_PROMPT_SYSTEM},
            {"role": "user", "content": snippets_text},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,  # gemma uses ~400 reasoning tokens before emitting output
    }).encode("utf-8")
    req = urllib.request.Request(
        LM_STUDIO_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"].strip()
            if not content:
                return None
            # Strip markdown code fences (model may wrap JSON in ```json ... ```)
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(
                    l for l in lines if not l.startswith("```")
                ).strip()
            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                return None
            return json.loads(content[start:end])
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
        return None


def run() -> dict:
    files = _get_recent_files()
    if not files:
        result = {"score": None, "reason": "no_files", "files_scored": 0}
        _write(result)
        return result

    snippets = [_read_snippet(f) for f in files]
    snippets_text = "\n\n".join(s for s in snippets if s)

    lm_result = _call_lm_studio(snippets_text)
    if lm_result is None:
        result = {"score": None, "reason": "offline_or_parse_error", "files_scored": len(files)}
        _write(result)
        return result

    score = lm_result.get("score")
    if not isinstance(score, (int, float)) or not (0 <= score <= 100):
        score = None

    result = {
        "score": int(score) if score is not None else None,
        "top_issue": lm_result.get("top_issue", ""),
        "files_scored": len(files),
        "files": [str(f.relative_to(REPO_ROOT)) for f in files],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(result)
    return result


def _write(data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = str(OUTPUT) + ".tmp"
    Path(tmp).write_text(json.dumps(data, indent=2), encoding="utf-8")
    Path(tmp).replace(OUTPUT)


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("score") is not None else 1)
