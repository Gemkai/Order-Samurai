# Gemini CLI hook payload fixtures

Shape of the JSON Gemini CLI sends on stdin to `BeforeTool` command hooks. Identifiers,
timestamps and paths are placeholders.

Provenance: **documentation-derived, no live Gemini CLI capture yet.** Built from
https://github.com/google-gemini/gemini-cli/blob/main/docs/hooks/reference.md (checked
2026-10-07): base fields `session_id`, `transcript_path`, `cwd`, `hook_event_name`,
`timestamp`; `BeforeTool` adds `tool_name`, `tool_input` (an object of the model's raw
arguments), optional `mcp_context` and `original_request_name`. Tool names and argument
names (`run_shell_command` with `command`; `write_file` with `file_path`, `content`;
`replace` with `file_path`, `old_string`, `new_string`, `instruction`; MCP tools named
`mcp_<server>_<tool>`) come from docs/reference/tools.md and docs/reference/configuration.md.
The documentation does not give the fields inside `mcp_context`, so the one here is a guess;
the guard does not read it. The guard scans every `tool_input` value, so exact key names do
not matter. Replace these with scrubbed live captures when one is recorded (see the PR's
live-proof checklist); keep the field set identical.
