"""The platform adapter — the 4 slots (RUNTIME_ROOT, TELEMETRY_SOURCE, verifiers,
surface_matrix) that isolate everything platform-specific. Above this, the aggregator,
scorecard, doctor, and surface matrix stay platform-neutral.

(Named `adapter`, not `platform`, to avoid shadowing the stdlib `platform` module.)

Fail-loud contract: a requested platform whose runtime root is absent raises
PlatformUnavailable. It NEVER silently substitutes a local copy — that silent
`if CORE_ROOT.exists()` fallback cost Jarvis three debugging sessions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .core_types import VerifierResult

_REGISTRY_PATH = Path(__file__).parent / "platforms.json"
# agentica_core lives under the Governance layer dir.
_GOVERNANCE_DIR = Path(__file__).resolve().parent.parent

Verifier = Callable[..., "list[VerifierResult]"]


class PlatformUnavailable(RuntimeError):
    """Requested platform is unknown or its runtime root does not exist.
    Raised instead of falling back silently."""


class AmbiguousPlatform(RuntimeError):
    """Auto-detection found more than one available platform; caller must choose."""


def _expand(spec: str) -> Path:
    spec = spec.replace("{home}", str(Path.home()))
    spec = spec.replace("{governance}", str(_GOVERNANCE_DIR))
    return Path(spec).expanduser()


@dataclass(frozen=True)
class ExecutionCapability:
    """Slot E (C1) — what this platform's HEADLESS execution surface supports.

    Each field names the flag or mechanism that provides the capability, or is None
    when the surface does not have it. It is deliberately not an argv: the exact
    argument vector belongs to the adapter that spawns the process, so this registry
    cannot drift away from the real spawn (Anti-Pattern #2).

    `structured_output` is the field that earns this slot its keep. It is None for
    claude — the CLI has --output-format json (an envelope) but nothing that
    constrains the model's own payload to a schema — which is precisely why the B3
    armed run needed a tolerant `extractJsonPayload` to survive prose-wrapped agent
    output. codex's --output-schema does constrain it, and the B0 preflight validated
    that it accepts the Phase A draft-07 schemas verbatim. A caller reads this field
    to decide which of those two worlds it is in.
    """

    invoke: str
    streaming: str | None
    cancel: str | None
    resume: str | None
    structured_output: str | None


_EXECUTION_SLOTS = ("invoke", "streaming", "cancel", "resume", "structured_output")


def _build_execution(spec: dict | None) -> ExecutionCapability | None:
    """None (or an explicit `"execution": null`) means this platform has no headless
    execution surface wired into the harness — antigravity today. A PARTIAL block is a
    config error, not a partial capability: a missing key would read as "unsupported"
    and silently disable a capability the platform actually has, so it raises."""
    if spec is None:
        return None
    missing = [k for k in _EXECUTION_SLOTS if k not in spec]
    if missing:
        raise ValueError(
            f"execution descriptor is missing required slot(s) {missing}; "
            f"declare them explicitly (use null for unsupported)"
        )
    return ExecutionCapability(**{k: spec[k] for k in _EXECUTION_SLOTS})


@dataclass(frozen=True)
class PlatformAdapter:
    name: str
    runtime_root: Path                  # slot A
    telemetry_source: Path              # slot B
    surface_matrix: Path                # slot D
    verifiers: tuple[Verifier, ...] = field(default=())  # slot C — populated in a later phase
    execution: ExecutionCapability | None = None  # slot E — None = no headless surface

    def available(self) -> bool:
        return self.runtime_root.exists()


def _load_registry() -> dict:
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))["platforms"]


def _build(name: str, spec: dict) -> PlatformAdapter:
    return PlatformAdapter(
        name=name,
        runtime_root=_expand(spec["runtime_root"]),
        telemetry_source=_expand(spec["telemetry_source"]),
        surface_matrix=_expand(spec["surface_matrix"]),
        execution=_build_execution(spec.get("execution")),
    )


def list_platforms() -> list[str]:
    return sorted(_load_registry().keys())


def resolve_platform(name: str | None = None) -> PlatformAdapter:
    """Resolve a platform adapter.

    name given  -> that platform, or PlatformUnavailable if unknown / root missing.
    name omitted-> auto-detect: exactly one available root, else PlatformUnavailable
                   (none) or AmbiguousPlatform (more than one).
    """
    registry = _load_registry()

    if name is not None:
        if name not in registry:
            raise PlatformUnavailable(
                f"unknown platform {name!r}; known: {sorted(registry)}"
            )
        adapter = _build(name, registry[name])
        if not adapter.runtime_root.exists():
            raise PlatformUnavailable(
                f"platform {name!r} runtime root does not exist: {adapter.runtime_root} "
                f"(refusing to fall back silently)"
            )
        return adapter

    available = [n for n in registry if _build(n, registry[n]).runtime_root.exists()]
    if not available:
        raise PlatformUnavailable(
            f"no known platform runtime root exists (checked: {sorted(registry)})"
        )
    if len(available) > 1:
        raise AmbiguousPlatform(
            f"multiple platforms available: {sorted(available)}; pass name= explicitly"
        )
    return _build(available[0], registry[available[0]])
