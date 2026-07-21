# Charter - Brush (Architecture & Token Efficiency)
North-star: orchestration surface and token economics fully measured.
This is the pillar most directly serving the save-tokens goal.
Measurement: python execution/doctor.py
Acceptance: same 7 criteria as Bow.
Caution: changes to config/*scorecard*.json or *policy*.json alter metric MEANING.
Treat as Claude-only judgment via explicit backlog item.

Highest-value candidates (sharpest token levers):
- BRUSH-001: mcp_or_cli field -> MCP-vs-CLI Ratio LIVE (MCP ~35x tokens) - value 10, effort 3
- BRUSH-002: subagent_spawns + parent_task -> Subagent Cost Multiplier LIVE (7-10x) - value 9, effort 3
- BRUSH-003: model + skill_tier -> Model Selection Adherence Opus<20% - value 8, effort 3

Baseline: Brush = 11 LIVE (2026-06-02)
