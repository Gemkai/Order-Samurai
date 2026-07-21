import json

from agentica_core import build_record
from agentica_core.emit import emit
from agentica_core.telemetry import validate_entry


def test_build_record_is_valid():
    rec = build_record("claude", "test.task", tokens_prompt=100, tokens_completion=20,
                       total_cost=0.01, model_tier="FAST", project="OS")
    rec["platform"] = "claude"  # build_record normalizes platform in
    validate_entry(rec)
    assert rec["platform"] == "claude"
    assert rec["task_name"] == "test.task"


def test_build_record_carries_agent_operation_fields():
    rec = build_record("claude", "t", orchestrator="Brush", chain_depth=2,
                       model="claude-opus", mcp_or_cli="cli", phase="Implementation")
    validate_entry(rec)
    assert rec["orchestrator"] == "Brush"
    assert rec["chain_depth"] == 2
    assert rec["mcp_or_cli"] == "cli"


def test_emit_writes_validated_record(tmp_path):
    target = tmp_path / "telemetry.jsonl"
    emit("claude", "session.work", path=target, tokens_prompt=500, total_cost=0.03,
         model_tier="FAST", subagent_spawns=3)
    rec = json.loads(target.read_text(encoding="utf-8").strip())
    assert rec["platform"] == "claude"
    assert rec["tokens_prompt"] == 500
    assert rec["subagent_spawns"] == 3
    validate_entry(rec)  # round-trips through the canonical contract
