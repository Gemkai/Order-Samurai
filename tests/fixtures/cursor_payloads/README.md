# Cursor hook payload fixtures

Shape of the JSON Cursor sends on stdin to `beforeShellExecution`,
`beforeMCPExecution` and `preToolUse` command hooks. Identifiers and paths are
placeholders.

Provenance: **documentation-derived, no live Cursor capture yet.** Built from
https://cursor.com/docs/hooks (checked 2026-10-07): base fields
`conversation_id`, `generation_id`, `model`, `hook_event_name`, `cursor_version`,
`workspace_roots`, `user_email`, `transcript_path`; `beforeShellExecution` adds
`command`, `cwd`, `sandbox`; `beforeMCPExecution` adds `tool_name`, `mcp_server_name`
and `tool_input` as a JSON-encoded **string**; `preToolUse` adds `tool_name`,
`tool_input` (an object) and `tool_use_id`. The documentation gives no concrete
`tool_input` keys for the `Write` tool, so `pre_tool_use_write.json` uses
`file_path` and `contents` as a guess; the guard scans every `tool_input` value, so
the key names do not matter. Replace these with scrubbed live captures when one is
recorded (see the PR's live-proof checklist); keep the field set identical.
