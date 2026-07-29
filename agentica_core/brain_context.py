"""Brain³ context loader — makes stateless models (local Ollama, cloud fallbacks)
able to plug into the shared brain.

File-reading harnesses (Claude Code, Antigravity, Codex) bootstrap the shared brain
by reading SHARED_NOTES.md at repo root. A raw model API call can't read files, so the
*caller* injects context instead. This module is that injection point: it loads the
Brain³ Foundation layer (the `Knowledge/vault/me/` identity portfolio) plus the
long-term memory index, and returns a compact system-prompt preamble.

Canonical source is the vault directory itself — this globs it rather than hardcoding a
filename list, so it never drifts from what SHARED_NOTES.md points at.

Usage:
    from agentica_core.brain_context import load_brain_context
    preamble = load_brain_context()          # identity portfolio + memory index
    text = call_llm(system=preamble + my_system, user=..., task=...)

Or via the router directly: call_llm(..., brain=True).
"""
from __future__ import annotations

import os
from pathlib import Path


def _repo_root(start: Path | None = None) -> Path | None:
    """Walk up from this file until the Brain³ Foundation dir is found."""
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / "Knowledge" / "vault" / "me").is_dir():
            return parent
    return None


def load_brain_context(max_chars: int = 4000, include_memory_index: bool = True) -> str:
    """Return a compact Brain³ preamble for injection into a model's system prompt.

    Reads the `Knowledge/vault/me/` identity portfolio (Foundation layer) and, by
    default, the long-term-memory index. Truncates to ``max_chars`` so a small local
    model isn't flooded. Returns "" if the brain can't be located — callers should
    treat an empty preamble as "no shared context available", never as an error.
    """
    root = _repo_root()
    if root is None:
        return ""

    parts: list[str] = ["# Shared Brain (<BRAND>³) — who you are working with\n"]

    me_dir = root / "Knowledge" / "vault" / "me"
    if me_dir.is_dir():
        # index.md first (it orients the rest), then the remaining portfolio files.
        files = sorted(me_dir.glob("*.md"))
        files.sort(key=lambda p: (p.name != "index.md", p.name))
        for f in files:
            try:
                body = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if body:
                parts.append(f"## {f.stem}\n{body}\n")

    if include_memory_index:
        mem_index = Path(
            os.path.expanduser(
                "~/.claude/projects/-Users-exampleuser-AgenticaOS/memory/MEMORY.md"
            )
        )
        if mem_index.is_file():
            try:
                parts.append(
                    "## long-term-memory-index\n"
                    + mem_index.read_text(encoding="utf-8").strip()
                    + "\n"
                )
            except OSError:
                pass

    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n\n[...brain context truncated...]"
    return text


if __name__ == "__main__":  # smoke check
    ctx = load_brain_context()
    print(f"loaded {len(ctx)} chars")
    print(ctx[:600])
