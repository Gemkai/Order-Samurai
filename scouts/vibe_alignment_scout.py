"""Anti-slop vibe alignment scout.

Purpose: score recently modified source files for AI slop density. Writes
  state/vibe_alignment.json with a 0-100 score for the aggregate to read.

Owner: arts-pillar
Inputs:
  - Recently modified .py/.ts files (top 5 by mtime) in execution/, scouts/, agentica_core/
  - LLM via agentica_core.model_router (Claude → Gemini → Ollama → OpenRouter)
Outputs:
  - state/vibe_alignment.json
Failure modes:
  - All LLM backends unavailable: writes score=null, reason="offline" — metric returns 0.0
  - No source files found: writes score=null, reason="no_files"
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[1]
import sys as _sys
if str(REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(REPO_ROOT))

# agentica_core is the canonical Governance kernel (parents[2]), not this repo.
_GOVERNANCE = _HERE.parents[2]
if str(_GOVERNANCE) not in _sys.path:
    _sys.path.insert(0, str(_GOVERNANCE))

from agentica_core.model_router import call_llm

STATE_DIR = REPO_ROOT / "state"
OUTPUT = STATE_DIR / "vibe_alignment.json"

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


def _call_model(snippets_text: str) -> dict | None:
    # max_tokens=2048 covers reasoning-token overhead on models that think before emitting.
    raw = call_llm(
        system=SLOP_PROMPT_SYSTEM,
        user=snippets_text,
        task="classification",
        max_tokens=2048,
        temperature=0.0,
    )
    if not raw:
        return None
    # Strip markdown code fences (some models wrap JSON in ```json ... ```)
    content = raw.strip()
    if content.startswith("```"):
        content = "\n".join(l for l in content.splitlines() if not l.startswith("```")).strip()
    start = content.find("{")
    end = content.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(content[start:end])
    except json.JSONDecodeError:
        return None


def run() -> dict:
    files = _get_recent_files()
    if not files:
        result = {"score": None, "reason": "no_files", "files_scored": 0}
        _write(result)
        return result

    snippets = [_read_snippet(f) for f in files]
    snippets_text = "\n\n".join(s for s in snippets if s)

    lm_result = _call_model(snippets_text)
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
