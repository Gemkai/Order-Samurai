"""Agentica OS platform-agnostic kernel.

The control-plane contracts the four layers obey: a platform adapter (so Governance can
observe any runtime) and the canonical telemetry schema (the Data-layer contract).
"""
from __future__ import annotations

from .adapter import (
    AmbiguousPlatform,
    PlatformAdapter,
    PlatformUnavailable,
    list_platforms,
    resolve_platform,
)
from .telemetry import (
    AUTONOMIC_EVENTS,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    TelemetryValidationError,
    append_entry,
    append_event,
    normalize_entry,
    validate_entry,
    validate_event,
    validate_metric,
)
from .types import VerifierResult
from .verifiers import load_verifiers, run_all as run_verifiers, summarize as summarize_verifiers
from .emit import build_record, emit

# NOTE: `doctor` is intentionally not imported here — it is run via `python -m agentica_core.doctor`,
# and importing it in __init__ triggers a RuntimeWarning (double-import). Use `from agentica_core.doctor import run_doctor`.

__all__ = [
    "AmbiguousPlatform",
    "PlatformAdapter",
    "PlatformUnavailable",
    "list_platforms",
    "resolve_platform",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "TelemetryValidationError",
    "append_entry",
    "append_event",
    "normalize_entry",
    "validate_entry",
    "validate_event",
    "validate_metric",
    "AUTONOMIC_EVENTS",
    "OPTIONAL_FIELDS",
    "VerifierResult",
    "load_verifiers",
    "run_verifiers",
    "summarize_verifiers",
    "build_record",
    "emit",
]
