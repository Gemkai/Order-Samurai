"""Model families used by the governance routing tiers (not a price table)."""
from __future__ import annotations

import re


KNOWN_TIERS = {
    "PREMIUM", "STANDARD", "FAST", "LOCAL", "FREE", "CLOUD", "REVIEW", "MIXED",
}


def model_tier(model: str | None, declared: str | None = None) -> str:
    tier = str(declared or "").strip().upper()
    if tier in KNOWN_TIERS - {"CLOUD"}:
        return tier
    name = str(model or "").strip().lower()
    if re.search(r"(?:^|[-/])(opus|fable|mythos)(?:[-/]|$)", name):
        return "PREMIUM"
    if "sonnet" in name:
        return "STANDARD"
    if "haiku" in name:
        return "FAST"
    if name == "codex-auto-review":
        return "REVIEW"
    if name.startswith("gpt-"):
        if any(part in name for part in ("mini", "nano", "luna", "spark")):
            return "FAST"
        if "terra" in name:
            return "STANDARD"
        return "PREMIUM"
    if name.startswith("gemini-"):
        return "FAST" if "flash" in name else "PREMIUM" if "pro" in name else "CLOUD"
    if name.startswith(("gemma", "qwen", "nomic-")):
        return "LOCAL"
    return tier if tier in KNOWN_TIERS else "unknown"
