"""Gemini platform verifier provider.

Gemini CLI telemetry governance: resolve the tracked surface matrix, check core
runtime surfaces, and validate recent telemetry records against the canonical schema.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..adapter import resolve_platform
from ..telemetry import normalize_entry, validate_entry
from ..types import VerifierResult


def _result(status: str, label: str, detail: str) -> VerifierResult:
    return {"status": status, "label": label, "detail": detail}


def _surface_path(runtime_root: Path, raw: str) -> Path:
    path = Path(raw)
    if raw == ".":
        return runtime_root
    if path.is_absolute():
        return path
    return runtime_root / path


def run_checks() -> list[VerifierResult]:
    profile = os.environ.get("ORDER_SAMURAI_AUDIT_PROFILE", "baseline").lower()
    platform = resolve_platform("gemini")
    root = platform.runtime_root
    results: list[VerifierResult] = []

    if not root.exists():
        if profile == "full":
            results.append(_result("FAIL", "gemini-runtime-root", f"missing runtime root: {root}"))
        else:
            results.append(_result("WARN", "gemini-runtime-root", f"runtime root not yet created: {root}"))
        return results

    results.append(_result("OK", "gemini-runtime-root", f"runtime root exists: {root}"))

    matrix_path = platform.surface_matrix
    if not matrix_path.exists():
        return [
            _result("FAIL", "gemini_surface_matrix.json", f"missing surface matrix: {matrix_path}")
        ]

    try:
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [_result("FAIL", "gemini_surface_matrix.json", f"invalid JSON: {exc}")]

    surfaces = matrix.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        results.append(_result("FAIL", "gemini_surface_matrix.json", "surfaces must be a non-empty list"))
    else:
        missing = []
        escaped = []
        for surface in surfaces:
            raw = str(surface.get("path", ""))
            target = _surface_path(root, raw).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                escaped.append(raw)
            if not target.exists():
                missing.append(raw)
        if escaped:
            results.append(_result("FAIL", "gemini-surfaces-contained", f"surface(s) escape runtime root: {', '.join(escaped)}"))
        else:
            results.append(_result("OK", "gemini-surfaces-contained", "all relative surfaces stay under the Gemini runtime root"))
        if missing:
            results.append(_result("WARN", "gemini-surfaces-resolve", f"missing optional/runtime surface(s): {', '.join(missing)}"))
        else:
            results.append(_result("OK", "gemini-surfaces-resolve", "all declared Gemini surfaces resolve"))

    telemetry = platform.telemetry_source
    if not telemetry.exists():
        if profile == "full":
            results.append(_result("FAIL", "gemini-telemetry", f"missing telemetry source: {telemetry}"))
        else:
            results.append(_result("WARN", "gemini-telemetry", f"telemetry source not yet created: {telemetry}"))
        return results

    lines = [line.strip() for line in telemetry.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
    if not lines:
        results.append(_result("WARN", "gemini-telemetry", "telemetry source exists but has no records yet"))
        return results

    bad = 0
    checked = 0
    for line in lines[-25:]:
        checked += 1
        try:
            validate_entry(normalize_entry(json.loads(line), platform="gemini"))
        except Exception:
            bad += 1
    if bad:
        results.append(_result("FAIL", "gemini-telemetry-schema", f"{bad}/{checked} recent records failed canonical schema validation"))
    else:
        results.append(_result("OK", "gemini-telemetry-schema", f"{checked} recent records validate against agentica.1"))

    return results


def get_verifiers() -> list:
    return [run_checks]
