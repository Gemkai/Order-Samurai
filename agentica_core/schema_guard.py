"""Warn-only schema validation for the agent-output artifacts (Phase A2).

Mirrors the wid_payload seam (`aggregate.validate_payload`): one JSON Schema file
under `Governance/schema/` is the single authority, the Python producer checks it
before persisting, and the TypeScript consumer checks the SAME file on startup.

The difference is the enforcement setting. `validate_payload` raises — a malformed
wid_payload envelope must never reach disk. Here the rows are *agent output*, and
tightening a contract against live agents can reject historically-valid output and
silently stall remediation. So this module is WARN-ONLY by design: it records the
violation and returns; the caller still writes the row. A3 flips to enforce after
7 clean days, and that flip is a change to the CALLER, not to this module.

Schemas are draft-07 and must stay draft-07: the TS half validates with ajv 6.15.0,
which rejects 2020-12 outright, while Python jsonschema 4.26 accepts it — so a
2020-12 schema passes every test here and fails only in production TS.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

# A parsed-JSON value. Loose on purpose — the whole point is to accept whatever an
# agent actually returned and let the schema, not the type checker, judge it.
Json = dict | list | str | int | float | bool | None

_THIS = Path(__file__).resolve()
SCHEMA_DIR = _THIS.parents[1] / "schema"

# The warn-only sink. A3's flip gate is `grep schema_violation` over 7 days of
# this file, so violations must be durably greppable, not just printed into a
# scheduled-task log that rotates.
VIOLATIONS_FILENAME = "schema_violations.jsonl"


def load_schema(name: str) -> dict:
    """Load `Governance/schema/<name>.schema.json` and assert it is valid draft-07."""
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return schema


def violations(instance: Json, schema_name: str) -> list[str]:
    """Return every schema violation in `instance` as a human-readable string.

    Empty list == valid. All errors, not just the first: an agent that got two
    fields wrong should be reported once with both, not fixed one round-trip at
    a time.
    """
    validator = jsonschema.Draft7Validator(load_schema(schema_name))
    return [
        f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(instance)
    ]


def check_warn_only(instance: Json, schema_name: str, sink_dir: Path,
                    context: dict | None = None) -> list[str]:
    """Validate, record any violation to the sink, and return the messages.

    NEVER raises — not on a violation (the caller writes its row regardless; that
    is the whole point of the warn-only window) and not on an unwritable sink
    (observability must not be able to break the write path it observes). A
    non-empty return is the signal A3's flip acts on.

    The recorded row carries the offending instance, so a violation is diagnosable
    without re-running the cycle that produced it.
    """
    try:
        messages = violations(instance, schema_name)
    except Exception as exc:  # noqa: BLE001
        # A missing, unreadable, malformed, or wrong-draft schema means the row is
        # UNVALIDATED, never valid. Keep the warn-only caller alive, but return and
        # persist a non-empty violation so the A3 clean-streak cannot advance while
        # its validator is broken.
        messages = [f"<schema>: validator unavailable: {type(exc).__name__}: {exc}"]
    if not messages:
        return []
    row = {
        "event": "schema_violation",
        "ts": datetime.now(timezone.utc).isoformat(),
        "schema": schema_name,
        "violations": messages,
        "instance": instance,
        **(context or {}),
    }
    try:
        sink_dir.mkdir(parents=True, exist_ok=True)
        with (sink_dir / VIOLATIONS_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
    return messages
