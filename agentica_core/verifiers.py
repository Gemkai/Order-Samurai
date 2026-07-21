"""Slot C — the generic verifier runner. Platform-neutral: it loads a platform's
verifier provider (named in platforms.json), runs each verifier, and normalizes
every result into the VerifierResult contract.

Reliability rule: a verifier that raises becomes a single FAIL result, never a total
abort — one broken check must not blind the whole doctor.
"""
from __future__ import annotations

import importlib
from typing import Callable

from .types import VerifierResult

Verifier = Callable[[], "list[VerifierResult]"]
_VALID_STATUS = {"OK", "WARN", "FAIL"}


def _registry_entry(platform_name: str) -> dict:
    from .adapter import PlatformUnavailable, _load_registry

    registry = _load_registry()
    if platform_name not in registry:
        raise PlatformUnavailable(f"unknown platform {platform_name!r}; known: {sorted(registry)}")
    return registry[platform_name]


def load_verifiers(platform_name: str) -> list[Verifier]:
    """Resolve the platform's verifier provider (a `module:callable` returning a list of
    run_checks-style callables). Returns [] when a platform declares no verifiers yet."""
    spec = _registry_entry(platform_name).get("verifiers")
    if not spec:
        return []
    module_path, _, attr = spec.partition(":")
    provider = getattr(importlib.import_module(module_path), attr)
    return list(provider())


def normalize_result(raw: object, source: str) -> VerifierResult:
    if isinstance(raw, dict) and "status" in raw and "label" in raw:
        status = raw.get("status")
        if status not in _VALID_STATUS:
            return {"status": "FAIL", "label": str(raw.get("label", source)),
                    "detail": f"invalid status {status!r} from {source}"}
        return {"status": status, "label": str(raw["label"]), "detail": str(raw.get("detail", ""))}
    return {"status": "FAIL", "label": source, "detail": f"malformed verifier result: {raw!r}"}


def _source_name(vf: Verifier) -> str:
    return f"{getattr(vf, '__module__', '?')}.{getattr(vf, '__qualname__', getattr(vf, '__name__', 'verifier'))}"


def run_all(verifiers: list[Verifier]) -> list[VerifierResult]:
    results: list[VerifierResult] = []
    for vf in verifiers:
        source = _source_name(vf)
        try:
            raw_results = vf()
        except Exception as exc:  # crashing verifier -> FAIL, not abort
            results.append({"status": "FAIL", "label": source, "detail": f"verifier raised: {exc!r}"})
            continue
        for raw in raw_results:
            results.append(normalize_result(raw, source))
    return results


def summarize(results: list[VerifierResult]) -> tuple[dict, int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    exit_code = 1 if counts["FAIL"] else 0  # WARN does not fail the gate
    return counts, exit_code
