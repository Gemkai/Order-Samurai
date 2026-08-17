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

Runtime cost (M6, 2026-08-16): this check needs only metric names and their
is_simulated flags, but building them via aggregate() costs 30-68s (measured),
because the full dashboard payload rebuilds every view. So run_checks() prefers
the canonical payload the refresh cycle already wrote (schema-validated, with an
explicit freshness limit) and only falls back to building one, under a hard
budget. A timeout is reported as WARN — never OK, since "verified" is exactly
the claim a timed-out run cannot make.
"""
from __future__ import annotations

import os
import re
import sys
import threading
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

#: Reuse the canonical payload only when its own timestamp is at most this old.
#: The refresh cycle rewrites it about once a minute when the machine is awake,
#: so a healthy host always qualifies with two orders of magnitude to spare; a
#: host that slept past the limit falls back to building a payload. Chosen in
#: M6.2; adjust only with recorded evidence.
FRESH_PAYLOAD_MAX_AGE_S = 15 * 60

#: Hard budget for the fallback build (M6.3). The doctor run must never block on
#: this family: measured full builds cost 30-68s, so on a healthy host the
#: canonical payload path answers instead and this budget is never spent.
BUILD_BUDGET_S = 10


from execution.verifier_results import make_result as _make_result  # noqa: E402
from execution.verifier_results import summarize  # noqa: F401,E402  (re-exported: doctor imports it from here)


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


def check_payload_sources(payload: dict, registry: list[dict],
                          repo_root: Path) -> list[dict[str, str]]:
    """The pure check: given a payload and the registry, judge every LIVE metric.

    Extracted from run_checks() (M6.1) so the contract is testable without any
    payload acquisition — the result rows are identical however the payload was
    obtained, which is what makes the canonical-payload fast path below a pure
    optimization rather than a semantic change.
    """
    by_metric = {e["metric"]: e for e in registry}
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


def load_fresh_payload(max_age_s: float = FRESH_PAYLOAD_MAX_AGE_S,
                       path: Path | None = None) -> dict | None:
    """The canonical payload the refresh cycle wrote, or None when unusable.

    Liveness is judged from the payload's OWN `timestamp` field, never from file
    existence or mtime — the check exists to catch a LIVE envelope disagreeing
    with its declared source, so it still needs the canonical live/simulated
    classification, just not a fresh rebuild of it. Schema-invalid, unparseable,
    stale, or missing all mean None; the caller decides whether to build instead.
    """
    try:
        import json

        from agentica_core.aggregate import default_payload_path, validate_payload

        payload_path = path or default_payload_path()
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        validate_payload(payload)
        written = datetime.fromisoformat(str(payload["timestamp"]))
        if written.tzinfo is None:
            written = written.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - written).total_seconds()
        if not 0 <= age_s <= max_age_s:
            return None  # stale — or from the future, which is a clock lie, not fresher data
        return payload
    except Exception:  # noqa: BLE001 - any defect in the cached copy → build a real one
        return None


def build_payload() -> dict:
    """Fallback: build a fresh payload. Costs 30-68s (measured 2026-08-16)."""
    from agentica_core.aggregate import aggregate
    return aggregate(timestamp=datetime.now(timezone.utc).isoformat(),
                     write_history=False)


def _build_under_budget(builder, budget_s: float):
    """Run `builder` with a deadline. Returns (payload|None, error|None, timed_out).

    A daemon THREAD, deliberately not a subprocess: the requirement is that a
    timeout can never leave a child process behind, and a thread is not a child
    process — it dies with doctor's own exit. On timeout the thread may keep
    computing until then; doctor has already reported and moved on.
    """
    box: dict = {}

    def _run() -> None:
        try:
            box["payload"] = builder()
        except Exception as exc:  # noqa: BLE001 - reported as the check's FAIL detail
            box["error"] = exc

    worker = threading.Thread(target=_run, daemon=True,
                              name="verify-live-sources-build")
    worker.start()
    worker.join(budget_s)
    if worker.is_alive():
        return None, None, True
    return box.get("payload"), box.get("error"), False


def run_checks(repo_root: Path = REPO_ROOT, *,
               payload_loader=load_fresh_payload,
               payload_builder=build_payload,
               registry: list[dict] | None = None,
               budget_s: float = BUILD_BUDGET_S) -> list[dict[str, str]]:
    """Judge every LIVE metric's declared source. Fast path first, then bounded.

    `payload_loader`/`payload_builder`/`registry` are injectable (M6.1) so tests
    hand in payloads directly instead of monkeypatching module imports.
    """
    if registry is None:
        try:
            from agentica_core.ronin_metrics import REGISTRY
        except Exception as exc:  # noqa: BLE001
            return [_make_result("FAIL", "live-source-scan",
                                 f"could not load the metric registry: "
                                 f"{type(exc).__name__}: {exc}")]
        registry = list(REGISTRY)

    payload = payload_loader() if payload_loader is not None else None
    if payload is None:
        payload, error, timed_out = _build_under_budget(payload_builder, budget_s)
        if timed_out:
            return [_make_result(
                "WARN", "live-source-scan",
                f"no fresh canonical payload and building one exceeded the "
                f"{budget_s:.0f}s budget — live sources UNVERIFIED this run "
                f"(not a clean result; see FRESH_PAYLOAD_MAX_AGE_S)",
            )]
        if error is not None or payload is None:
            detail = (f"{type(error).__name__}: {error}" if error is not None
                      else "builder returned no payload")
            return [_make_result("FAIL", "live-source-scan",
                                 f"could not build payload to verify live sources: {detail}")]

    return check_payload_sources(payload, registry, repo_root)


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
