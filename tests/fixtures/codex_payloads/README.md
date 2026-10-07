# Codex PreToolUse payload fixtures

Shape of the JSON Codex sends on stdin to a `PreToolUse` command hook for the
`Bash` and `apply_patch` tools. Identifiers and paths are placeholders.

Provenance: built from the Codex hooks reference (common fields `session_id`,
`transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`; event
fields `turn_id`, `tool_name`, `tool_use_id`, `tool_input`; Bash and apply_patch
both carry their text in `tool_input.command`) and cross-checked against a Codex
PreToolUse adapter in daily use. Replace with a scrubbed live capture when one is
recorded (see the PR's live-proof checklist); keep the field set identical.
