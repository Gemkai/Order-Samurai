"""Honesty gate: every LIVE metric's declared source must resolve on disk.

P5 structural-honesty invariant. A metric the payload marks LIVE (is_simulated
False) must be backed by a source that actually exists — otherwise a dead or
unreachable source is being passed off as live data. That is the exact failure
mode that left the local-LLM tier reporting "green" for a month while it was
silently broken: something kept emitting a value after the source went away.

Scope (deliberate):
  - Only REGISTRY metrics whose `source` names CONCRETE filesystem path(s) are
    checkable. Logical sources — telemetry.* (read live from the telemetry
    stream), verifier.* (recomputed every run), and pure computations
    (len(REGISTRY)/...) — have no static file to stat and are not failable.
  - Only metrics the freshly-built payload marks LIVE are checked. A metric whose
    source is absent makes its reducer return None -> the metric is emitted
    SIMULATED, not LIVE, so it is correctly skipped (no false FAIL on a fresh
    checkout where runtime artifacts like cycle_*.json don't exist yet). The
    violation this gate catches is the DESYNC: payload says LIVE while the
    declared source is gone.

Freshness note: file mtime is intentionally NOT used as a staleness gate. The
concrete sources here are append-only event logs / state snapshots whose reducers
window events internally; an old mtime means "no recent events" (legitimately
quiescent), not "stale data." An mtime gate would FAIL on healthy quiet logs
(e.g. a loop-breaker log with no fires for weeks) and break the green baseline.
Existence is the honest, false-positive-free half of the invariant.

Source mini-language (as declared in agentica_core.ronin_metrics.REGISTRY):
  a + b          both required (conjunction)
  a | b          either suffices (alternation)
  file.mtime(g)  one or more comma-separated globs; any match satisfies
  path/with/*    glob; >=1 match satisfies
  ~/.claude/...   resolved under the user home; all other tokens under REPO_ROOT
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import REPO_ROOT

# agentica_core (the metric kernel + aggregator) lives one level up in Governance.
_GOVERNANCE = REPO_ROOT.parent
if str(_GOVERNANCE) not in sys.path:
    sys.path.insert(0, str(_GOVERNANCE))

_HOME_CLAUDE = Path(os.path.expanduser("~")) / ".claude"

# A source token is "logical" (live-computed, no static file) when it starts with
# one of these — such metrics are honest by construction every run.
_LOGICAL_PREFIXES = ("telemetry.", "verifier.", "len(")


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def _is_logical_source(source: str) -> bool:
    return source.strip().startswith(_LOGICAL_PREFIXES)


def _token_resolves(token: str, repo_root: Path) -> bool:
    """True if a single path token resolves to >=1 existing file."""
    token = token.strip()
    if not token:
        return True  # nothing to require
    if token.startswith("~/.claude/"):
        base, rel = _HOME_CLAUDE, token[len("~/.claude/"):]
    else:
        base, rel = repo_root, token
    if "*" in rel:
        return any(base.glob(rel))
    return (base / rel).exists()


def _source_missing_tokens(source: str, repo_root: Path) -> list[str]:
    """Return the conjunction tokens that fail to resolve. Empty list = satisfied.

    `+` (and commas inside file.mtime) join required tokens; within a required
    token, `|` lists interchangeable alternatives (any one satisfies).
    """
    inner = re.sub(r"^file\.mtime\((.*)\)$", r"\1", source.strip())
    missing: list[str] = []
    for required in re.split(r"[+,]", inner):
        required = required.strip()
        if not required:
            continue
        alternatives = [a for a in required.split("|") if a.strip()]
        if not any(_token_resolves(alt, repo_root) for alt in alternatives):
            missing.append(required)
    return missing


def _live_metric_names(payload: dict) -> set[str]:
    live: set[str] = set()
    for pillar in payload.get("pillars", {}).values():
        if not isinstance(pillar, dict):
            continue
        for group in pillar.values():
            if not isinstance(group, dict):
                continue
            for metric_name, env in group.items():
                if isinstance(env, dict) and not env.get("is_simulated"):
                    live.add(metric_name)
    return live


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    try:
        from agentica_core.aggregate import aggregate
        from agentica_core.ronin_metrics import REGISTRY
        payload = aggregate(timestamp=datetime.now(timezone.utc).isoformat(),
                            write_history=False)
    except Exception as exc:  # noqa: BLE001
        return [_make_result("FAIL", "live-source-scan",
                             f"could not build payload to verify live sources: "
                             f"{type(exc).__name__}: {exc}")]

    by_metric = {e["metric"]: e for e in REGISTRY}
    live = _live_metric_names(payload)

    offenders: list[str] = []
    checked = 0
    for metric_name in sorted(live):
        entry = by_metric.get(metric_name)
        if entry is None:
            continue  # not a REGISTRY metric (telemetry/verifier-derived elsewhere)
        source = str(entry.get("source", ""))
        if _is_logical_source(source):
            continue
        checked += 1
        missing = _source_missing_tokens(source, repo_root)
        if missing:
            offenders.append(f"{metric_name} (source unresolved: {', '.join(missing)})")

    if offenders:
        return [_make_result(
            "FAIL", "live-source-scan",
            "LIVE metric(s) whose declared source is missing: " + "; ".join(sorted(offenders)),
        )]
    return [_make_result(
        "OK", "live-source-scan",
        f"all {checked} path-backed LIVE metric(s) resolve to an existing source",
    )]


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
