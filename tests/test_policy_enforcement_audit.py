"""Eval for the deterministic policy-enforcement-audit mechanism
(bin/policy_enforcement_audit.py).

This IS the eval the LLM /policy-enforcement-audit skill never had: fixtures
drive classify_reader and run_audit through their deterministic rule logic, plus
idempotency checks (same input → same output; scanners called exactly once per
policy file). All filesystem access is injected — no test ever touches the real
~/.claude/ tree.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin.policy_enforcement_audit import (  # type: ignore[import-not-found]
    classify_reader,
    run_audit,
    suggest_fix,
)

FROZEN_TS = "2026-06-15T00:00:00+00:00"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ENFORCER_POLICY = {
    "path": "/fake/.claude/safety/hook_policies.json",
    "policy_type": "hook_policies",
    "keys": ["blocked_paths", "protected_files"],
}

_ALLOWLIST_POLICY = {
    "path": "/fake/.claude/data/allowlist_baseline.json",
    "policy_type": "allowlist_baseline",
    "keys": ["allowed_tools"],
}

_THRESHOLD_POLICY = {
    "path": "/fake/.claude/data/cost_threshold.json",
    "policy_type": "cost_threshold",
    "keys": ["max_daily_cost"],
}

_UNKNOWN_POLICY = {
    "path": "/fake/.claude/data/custom_rules.json",
    "policy_type": "custom_rules",
    "keys": ["rule_set"],
}

# Reader record factories
def _enforcer_reader(path: str = "/fake/hooks/guardrails.py") -> dict:
    return {
        "reader_path": path,
        "reader_type": "ENFORCER",
        "evidence": r"sys\.exit\([1-9]",
        "snippet": 'if violation:\n    sys.exit(2)',
    }


def _observer_reader(path: str = "/fake/scripts/audit_logger.py") -> dict:
    return {
        "reader_path": path,
        "reader_type": "OBSERVER",
        "evidence": r"json\.dump",
        "snippet": 'json.dump(results, f)',
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(
    policy_files: list[dict],
    readers_map: dict[str, list[dict]],
) -> dict:
    """Run audit with fully injected I/O — no real filesystem access."""
    return run_audit(
        policy_files_fn=lambda: policy_files,
        readers_fn=lambda path: readers_map.get(path, []),
        now_fn=lambda: FROZEN_TS,
    )


# ---------------------------------------------------------------------------
# ClassifyReaderTests
# ---------------------------------------------------------------------------

class ClassifyReaderTests(unittest.TestCase):

    def test_classifies_sys_exit_nonzero_as_enforcer(self) -> None:
        code = "if bad:\n    sys.exit(2)"
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_classifies_raise_with_policy_intent_as_enforcer(self) -> None:
        # PE-02 fix: raise is only ENFORCER when enforcement-intent word is present.
        code = "raise ValueError('policy violated')"
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_classifies_raise_blocked_by_policy_as_enforcer(self) -> None:
        code = "raise RuntimeError('blocked by policy')"
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_classifies_raise_key_error_as_observer(self) -> None:
        # PE-02 fix: incidental KeyError (dict lookup failure) is NOT enforcement.
        code = "value = data['key']  # raises KeyError if missing"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_sys_exit_zero_as_observer(self) -> None:
        # PE-01: success exit (exit 0) is NOT a policy block — it is an observer pattern.
        code = "if ok:\n    sys.exit(0)"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_return_false_as_enforcer(self) -> None:
        code = "def gate():\n    return False"
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_classifies_json_action_block_as_enforcer(self) -> None:
        code = '{"action": "block", "reason": "protected path"}'
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_classifies_json_dump_as_observer(self) -> None:
        code = "json.dump(results, f)"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_logger_call_as_observer(self) -> None:
        code = "logger.warning('policy file loaded')"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_print_without_exit_as_observer(self) -> None:
        code = "print('policy loaded')"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_score_call_as_observer(self) -> None:
        code = "score = count_violations(policy)"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_classifies_jsonl_write_as_observer(self) -> None:
        code = "with open('events.jsonl', 'a') as f: f.write(line)"
        self.assertEqual(classify_reader(code), "OBSERVER")

    def test_enforcer_pattern_takes_priority_over_observer_patterns(self) -> None:
        # Code that both logs AND exits non-zero — must be ENFORCER
        code = "logger.error('violation')\nsys.exit(2)"
        self.assertEqual(classify_reader(code), "ENFORCER")

    def test_empty_snippet_is_observer(self) -> None:
        self.assertEqual(classify_reader(""), "OBSERVER")


# ---------------------------------------------------------------------------
# SuggestFixTests
# ---------------------------------------------------------------------------

class SuggestFixTests(unittest.TestCase):

    def test_suggests_pretooluse_hook_for_protected_files(self) -> None:
        fix = suggest_fix("protected_files")
        self.assertIn("PreToolUse", fix)
        self.assertIn("hook", fix.lower())

    def test_suggests_pretooluse_hook_for_blocked_paths(self) -> None:
        fix = suggest_fix("blocked_paths")
        self.assertIn("PreToolUse", fix)

    def test_suggests_allowlist_gate_for_allowlist(self) -> None:
        fix = suggest_fix("allowlist_baseline")
        self.assertIn("allowlist", fix.lower())
        self.assertIn("gate", fix.lower())

    def test_suggests_metric_gate_for_threshold(self) -> None:
        fix = suggest_fix("cost_threshold")
        self.assertIn("metric gate", fix.lower())

    def test_suggests_generic_gate_for_unknown_type(self) -> None:
        fix = suggest_fix("custom_rules")
        self.assertIn("non-zero exit", fix.lower())

    def test_suggests_generic_gate_for_empty_type(self) -> None:
        fix = suggest_fix("")
        self.assertIn("gate", fix.lower())


# ---------------------------------------------------------------------------
# RunAuditTests
# ---------------------------------------------------------------------------

class RunAuditTests(unittest.TestCase):

    def test_marks_enforced_when_any_reader_is_enforcer(self) -> None:
        report = _run(
            [_ENFORCER_POLICY],
            {_ENFORCER_POLICY["path"]: [_enforcer_reader(), _observer_reader()]},
        )
        finding = report["findings"][0]
        self.assertEqual(finding["verdict"], "ENFORCED")
        self.assertTrue(finding["has_enforcer"])

    def test_marks_unenforced_when_all_readers_are_observers(self) -> None:
        report = _run(
            [_ALLOWLIST_POLICY],
            {_ALLOWLIST_POLICY["path"]: [_observer_reader()]},
        )
        finding = report["findings"][0]
        self.assertEqual(finding["verdict"], "DECLARED_BUT_UNENFORCED")
        self.assertFalse(finding["has_enforcer"])
        self.assertIn(_ALLOWLIST_POLICY["path"], report["needs_review"])

    def test_marks_not_read_when_no_readers_found(self) -> None:
        report = _run([_THRESHOLD_POLICY], {})
        finding = report["findings"][0]
        self.assertEqual(finding["verdict"], "NOT_READ")
        self.assertEqual(finding["readers"], [])

    def test_counts_are_correct_for_mixed_findings(self) -> None:
        policies = [_ENFORCER_POLICY, _ALLOWLIST_POLICY, _THRESHOLD_POLICY]
        readers_map = {
            _ENFORCER_POLICY["path"]: [_enforcer_reader()],
            _ALLOWLIST_POLICY["path"]: [_observer_reader()],
            # THRESHOLD_POLICY has no readers
        }
        report = _run(policies, readers_map)
        self.assertEqual(report["counts"]["enforced"], 1)
        self.assertEqual(report["counts"]["unenforced"], 1)
        self.assertEqual(report["counts"]["not_read"], 1)
        self.assertEqual(report["policies_scanned"], 3)

    def test_fix_suggestion_present_for_unenforced(self) -> None:
        report = _run(
            [_ALLOWLIST_POLICY],
            {_ALLOWLIST_POLICY["path"]: [_observer_reader()]},
        )
        fix = report["findings"][0]["fix_suggestion"]
        self.assertTrue(fix, "fix_suggestion should be non-empty for unenforced policy")

    def test_fix_suggestion_empty_for_enforced(self) -> None:
        report = _run(
            [_ENFORCER_POLICY],
            {_ENFORCER_POLICY["path"]: [_enforcer_reader()]},
        )
        self.assertEqual(report["findings"][0]["fix_suggestion"], "")

    def test_needs_review_lists_only_unenforced_paths(self) -> None:
        policies = [_ENFORCER_POLICY, _ALLOWLIST_POLICY, _THRESHOLD_POLICY]
        readers_map = {
            _ENFORCER_POLICY["path"]: [_enforcer_reader()],
            _ALLOWLIST_POLICY["path"]: [_observer_reader()],
        }
        report = _run(policies, readers_map)
        self.assertEqual(report["needs_review"], [_ALLOWLIST_POLICY["path"]])

    def test_generated_at_propagated_from_now_fn(self) -> None:
        report = _run([_ENFORCER_POLICY], {})
        self.assertEqual(report["generated_at"], FROZEN_TS)

    def test_empty_policy_list_produces_zero_findings(self) -> None:
        report = _run([], {})
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["policies_scanned"], 0)
        self.assertEqual(report["counts"], {"enforced": 0, "unenforced": 0, "not_read": 0})


# ---------------------------------------------------------------------------
# IdempotencyTests
# ---------------------------------------------------------------------------

class IdempotencyTests(unittest.TestCase):

    def _stable_run(self) -> dict:
        policies = [_ENFORCER_POLICY, _ALLOWLIST_POLICY, _THRESHOLD_POLICY]
        readers_map = {
            _ENFORCER_POLICY["path"]: [_enforcer_reader()],
            _ALLOWLIST_POLICY["path"]: [_observer_reader()],
        }
        return _run(policies, readers_map)

    def test_same_input_yields_identical_report(self) -> None:
        """Running twice with identical injected fns produces byte-identical output."""
        first = self._stable_run()
        second = self._stable_run()
        self.assertEqual(first, second)

    def test_no_mutations_no_double_action(self) -> None:
        """readers_fn is called exactly once per policy file — not twice."""
        call_counts: dict[str, int] = {}
        policies = [_ENFORCER_POLICY, _ALLOWLIST_POLICY]

        def counting_readers(path: str) -> list[dict]:
            call_counts[path] = call_counts.get(path, 0) + 1
            return []

        run_audit(
            policy_files_fn=lambda: policies,
            readers_fn=counting_readers,
            now_fn=lambda: FROZEN_TS,
        )
        # Each policy path should be queried exactly once
        for pf in policies:
            self.assertEqual(
                call_counts.get(pf["path"], 0),
                1,
                f"readers_fn called {call_counts.get(pf['path'], 0)}× for {pf['path']}, expected 1",
            )


if __name__ == "__main__":
    unittest.main()
