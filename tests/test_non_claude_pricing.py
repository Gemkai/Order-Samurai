"""Explicit non-Claude models cannot inherit Claude's fallback dollar estimate."""
import json
from pathlib import Path

import pytest

from bin.first_blood import build_report, estimate_cost, parse_transcript


@pytest.mark.parametrize("model", ["gpt-6-astra", "gemini-3", "openai/gpt-6-sol", "codex-mini-latest"])
def test_non_claude_models_have_no_price_or_spend_spike(tmp_path, model):
    path = tmp_path / "non-claude-session.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {
        "model": model, "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    }}) + "\n")
    record = parse_transcript(path)
    assert record is not None
    report = build_report([record])
    assert {
        "estimate": estimate_cost(model, 1_000_000, 1_000_000, 0),
        "record_cost": record["total_cost"],
        "report_cost": report["total_cost"],
        "spikes": report["spend_spikes"],
    } == {"estimate": None, "record_cost": None, "report_cost": None, "spikes": []}
    assert report["sessions_scanned"] == 1
    assert estimate_cost("mystery-model", 1_000_000, 0, 0) == 3.0
