"""One authoritative parser for reflex ids (LEDGER-003).

A reflex id comes in exactly four shapes, all of them live in exec_log.jsonl /
SENSEI_LEDGER.jsonl / autonomic_events.jsonl today:

    metric:<pillar>:<Metric_Name>       e.g. metric:bow:Error_Rate
    trajectory:<pillar>:<Metric_Name>   e.g. trajectory:arts:Slop_Density
    correlation:<label>                 e.g. correlation:cost_and_quality_tradeoff
    manual:<skill>                      e.g. manual:wiki

Only the first two carry a pillar and name a metric. The last two carry a LABEL in
segment 2 — a correlation name or a skill name — which is emphatically not a pillar
and not a metric. Every consumer used to hand-split with `reflex_id.split(":")` under
its own assumptions about which of those was true, and on 2026-08-14 that silently
dropped every stuck `correlation:*` reflex from the dashboard (skill_no_impact.py
assumed >= 3 segments). This module is the single knowledge-level home for the shape;
import it instead of splitting.

`metric` is deliberately None for correlation/manual — conflating the label with a
metric name is the bug this exists to prevent. A caller that wants to fall back to the
raw id already holds it.
"""
from __future__ import annotations

from typing import NamedTuple

# The prefixes whose ids encode `<kind>:<pillar>:<metric>`.
_METRIC_KINDS = ("metric", "trajectory")
# The prefixes whose ids encode `<kind>:<label>` and carry no pillar/metric.
_LABEL_KINDS = ("correlation", "manual")


class ReflexId(NamedTuple):
    """Parsed reflex id. `pillar`/`metric` are non-None only for metric-scoped kinds."""

    kind: str            # "metric" | "trajectory" | "correlation" | "manual" | "unknown"
    pillar: str | None   # pillar slug, metric-scoped kinds only
    metric: str | None   # metric name, metric-scoped kinds only


def parse_reflex_id(reflex_id: str) -> ReflexId:
    """Parse a reflex id into its kind and, when it has them, pillar + metric.

    A malformed metric-scoped id (`metric:bow`, `only_one`, "") is reported as
    kind "unknown" rather than a metric with missing parts — every call site
    already treated those as un-parseable, and claiming a kind we cannot honour
    would just move the guard somewhere else.
    """
    parts = (reflex_id or "").split(":")
    kind = parts[0]
    if kind in _METRIC_KINDS and len(parts) >= 3:
        # Segments 2+ rejoined: no live metric name contains ':', but rejoining
        # keeps the parse lossless rather than truncating a future one.
        return ReflexId(kind, parts[1] or None, ":".join(parts[2:]))
    if kind in _LABEL_KINDS and len(parts) >= 2 and parts[1]:
        return ReflexId(kind, None, None)
    return ReflexId("unknown", None, None)
