"""Shared, platform-neutral types. The one shape every platform's verifiers and the
doctor aggregator agree on, so doctor.py can consume verifiers from any platform."""
from __future__ import annotations

from typing import Literal, TypedDict

VerifierStatus = Literal["OK", "WARN", "FAIL"]


class VerifierResult(TypedDict):
    """A single check outcome. Both Order Samurai (Claude) and Jarvis (Antigravity)
    already emit this shape; this is the canonical declaration."""

    status: VerifierStatus
    label: str
    detail: str
