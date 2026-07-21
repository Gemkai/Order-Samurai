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

from .types import VerifierResult

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
    p = Path(spec).expanduser()
    if not p.exists() and "Order Samurai/Order Samurai" in p.as_posix():
        alternative = Path(p.as_posix().replace("Order Samurai/Order Samurai", "Order Samurai"))
        if alternative.exists():
            return alternative
    return p


@dataclass(frozen=True)
class PlatformAdapter:
    name: str
    runtime_root: Path                  # slot A
    telemetry_source: Path              # slot B
    surface_matrix: Path                # slot D
    verifiers: tuple[Verifier, ...] = field(default=())  # slot C — populated in a later phase

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
