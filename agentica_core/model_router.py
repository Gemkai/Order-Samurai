"""Compatibility facade for AgenticaOS's canonical LLM gateway.

The stable public import remains::

    from agentica_core.model_router import call_llm

Provider implementations, retries, timeouts, model rosters, privacy routing,
and empty-output handling now live only in :mod:`agentica_core.llm.gateway`.
Keeping this facade avoids breaking scouts and stateless callers while removing
the second independently evolving HTTP router that previously lived here.
"""
from __future__ import annotations

from agentica_core.llm.gateway import (
    CLOUD_TIMEOUT_SEC,
    ROUTED_MODELS,
    call_routed_llm,
)
from agentica_core.llm.local_guards import LOCAL_TIMEOUT_SEC

__router_facade__ = True

# Compatibility names retained for callers that introspect the documented
# roster/timeout. They point at the canonical gateway values, never copies.
_MODELS = ROUTED_MODELS
_TIMEOUT_S = CLOUD_TIMEOUT_SEC
call_llm = call_routed_llm

__all__ = ["call_llm"]
