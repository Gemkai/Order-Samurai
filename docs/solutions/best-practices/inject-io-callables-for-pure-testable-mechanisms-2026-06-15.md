---
title: Inject I/O callables to keep extracted mechanisms pure and fully testable
date: 2026-06-15
category: docs/solutions/best-practices/
module: dojo / reflex remediation
problem_type: best_practice
component: tooling
severity: medium
applies_when:
  - Extracting LLM skill logic into a deterministic standalone mechanism
  - The mechanism would otherwise require subprocess calls that complicate testing
  - You need full unit test coverage without process-level mocking
  - The mechanism must remain callable from both tests and live orchestration contexts
tags:
  - dependency-injection
  - testability
  - pure-functions
  - mechanism-extraction
  - subprocess
  - determinization
---

# Inject I/O callables to keep extracted mechanisms pure and fully testable

## Context

The `/pip-safe-upgrade` skill had an unmeasurable success rate as a reflex remediation: pure LLM
judgment on package upgrades is non-deterministic — identical inputs produce different outputs across
runs, making success/failure untestable and calibration impossible. Analysis of five prior manual runs
(auto memory [claude]) confirmed that ~90% of the decisions followed explicit, stateable rules:

- CVE-flagged packages always upgrade (unless ML-pinned)
- Already-at-target packages always skip
- Dry-run-detected downgrades always block
- ML marker packages (torch, transformers) trigger a constraint-aware mode

Delegating rule-based logic to an LLM adds latency, cost, and variance without any benefit. The gap:
a skill that *looks* automated but behaves like a coin flip under the reflex engine's load.

## Guidance

**Extract the 90% rule-based logic into a pure function with injected I/O.** The LLM becomes a
fallback for the genuinely ambiguous minority (novel constraint conflicts the rules cannot resolve),
not the primary execution path.

The extracted mechanism is a plain callable whose side-effectful operations (subprocess, network,
filesystem) are passed as parameters with real implementations as defaults:

```python
def run_plan(
    audit: dict,
    installed: set[str],
    *,
    tiers: set[str] | None = None,
    do_apply: bool = False,
    dry_run_fn: Callable[[str], str] = _real_dry_run,   # <-- injected I/O boundary
    apply_fn: Callable[[str], bool] = _real_apply,      # <-- injected I/O boundary
) -> dict:
    ...
```

Tests supply lightweight lambdas — no mocking, no `unittest.mock`, no `@pytest.fixture` monkeypatching:

```python
report = run_plan(
    audit,
    installed={"torch"},
    dry_run_fn=lambda name: f"Would install {name}-x",  # fixture: clean dry-run
    apply_fn=lambda name: True,
)
assert {r["name"] for r in report["applied"]} == {"certifi"}
assert {r["name"] for r in report["blocked"]} == {"torch"}
```

## Why This Matters

**I/O injection beats mocking** because mocking is a test-time workaround for code not designed to be
tested — it patches at the import level, is brittle to refactors, and couples test setup to
implementation details the test should not care about. Injection is a design decision: the function
declares its dependencies in its signature. No patching, no import-level side effects, no test
infrastructure required.

**The early-exit pattern matters for idempotency.** Reflex remediations run on a schedule. Checking
`already_current()` and `ml_hard_block()` *before* calling `dry_run_fn` means re-runs on a healthy
system cost nothing. Without this, every reflex trigger pays the full subprocess overhead even when
there is nothing to do, and "already done" becomes indistinguishable from "did something" in the
audit log.

**Before/after delta is now measurable.** Deterministic code with fixture inputs produces a
repeatable ground truth. The eval (`tests/test_pip_safe_upgrade.py`, 24 tests) caught two real bugs
before shipping:
1. `run_plan` called `dry_run_fn` even for already-at-target packages (should skip without shelling out)
2. In ML mode, passing `parsed=None` to `decide()` blocked *every* package, not just ML-pinned ones — clean CVE packages were wrongly blocked

## When to Apply

- The LLM skill driving a reflex has measurably low or unmeasurable success rate, and most of its
  decisions follow explicit, stateable rules
- The mechanism must be idempotent and re-entrant (runs on a schedule or in response to events, not
  once per human request)
- You need a before/after eval delta — deterministic code produces repeatable ground truth; LLM
  output does not
- The side effects (subprocess, network, filesystem) are the only non-deterministic part — the
  decision logic is pure given the inputs

## Examples

### Before: subprocess baked in, untestable without mocking

```python
def upgrade_package(name: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", name],
        capture_output=True, text=True, timeout=300,
    )
    return result.returncode == 0

# test must patch import — brittle
from unittest.mock import patch
def test_upgrade():
    with patch("mymodule.subprocess.run") as mock:
        mock.return_value.returncode = 0
        assert upgrade_package("requests") is True
```

### After: I/O injected, testable with lambdas

```python
def _real_apply(name: str) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", name],
        capture_output=True, text=True, timeout=300,
    )
    return proc.returncode == 0

def run_plan(
    audit: dict,
    installed: set[str],
    *,
    apply_fn: Callable[[str], bool] = _real_apply,
) -> dict:
    ...

# test: pure lambda, no patching needed
def test_records_upgraded_flag_when_apply_requested():
    report = run_plan(
        _audit(outdated=[_outdated("certifi", "2025.1.1", "2026.1.1")]),
        installed=set(),
        do_apply=True,
        dry_run_fn=lambda name: f"Would install {name}-x",
        apply_fn=lambda name: True,
    )
    assert report["applied"][0]["upgraded"] is True
```

### Tier filter: keep autonomous runs cheap

```python
# Reflex engine wires cve+security only — dry-runs a handful of packages, not the full 160
report = run_plan(audit, installed, tiers={"cve", "security"})
```

## Gotchas

1. **Injection depth**: inject only at the I/O boundary, not at every internal function. If you find
   yourself injecting a `sort_fn` or a `filter_fn`, the logic has leaked into the wrong layer — rule
   logic belongs inside the mechanism, not in the caller.

2. **The LLM fallback must still be wired**: extracting the 90% means the `blocked` path (the
   remaining 10%) needs a real handler. Surfacing it as a `blocked` result with a reason string is
   not the same as resolving it. Confirm the upstream consumer actually reads and acts on `blocked`
   entries before shipping.

3. **Early-exit short-circuits audit visibility**: `already_current()` returning before any pip call
   means the audit log shows nothing happened — correct, but indistinguishable from "never fired."
   Emit an explicit `no_op` status to the exec log so monitoring can distinguish "ran and found
   nothing to do" from "never fired."

4. **`mechanism` vs `command` duality in the reflex engine**: the reflex payload keeps both
   `command: /pip-safe-upgrade` (for eligibility checking, cooldown keys, and efficacy tracking in
   the TS engine) AND `mechanism: ['bin/pip_safe_upgrade.py', '--tiers', 'cve,security']` (the
   deterministic execution path). These are parallel fields — `mechanism` does not replace `command`.

## Related

- `bin/pip_safe_upgrade.py` — the reference implementation (branch `feat/determinize-pip-safe-upgrade`)
- `tests/test_pip_safe_upgrade.py` — 24-test eval harness (24/24 passing)
- `.mex/patterns/add-live-metric-to-dojo.md` — contains `@lru_cache` anti-pattern warning that also applies here: never cache functions that read files written by scouts/hooks mid-run
- `docs/solutions/best-practices/order-samurai-tui-ronin-toggle-pattern-2026-06-06.md` — prior best-practice doc in this project (unrelated topic, for orientation)
