"""Eval for the deterministic Secrets_Detected scrub mechanism (bin/secret_scrub.py).

Covers the detect->verify core (leaking-source count == metric, breach gate, ranking, idempotency,
dry-run is read-only in the report) and the redaction primitive (mask value, keep key name,
idempotent, skip placeholders). The kernel<->bin count parity lives in
agentica_core/tests/test_secret_scrub_drift.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]      # Order Samurai
GOV_ROOT = Path(__file__).resolve().parents[2]       # Governance (for agentica_core)
for _p in (REPO_ROOT, GOV_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bin.secret_scrub import (  # type: ignore[import-not-found]
    audit,
    redact_text,
    source_count,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _finding(source: str, pattern: str = "anthropic_key", masked: str = "sk-a****cdef") -> dict:
    return {"source": source, "pattern_name": pattern, "match_masked": masked}


_FIXED_NOW = lambda: "2026-06-29T00:00:00+00:00"


def _audit(findings, *, fail_threshold=1.0, **kw):
    return audit(findings, fail_threshold=fail_threshold, now_fn=_FIXED_NOW, **kw)


# ---------------------------------------------------------------------------
# source_count — the metric value
# ---------------------------------------------------------------------------

class SourceCount(unittest.TestCase):
    def test_counts_distinct_sources_not_findings(self):
        # 3 findings across 2 files -> count is 2 (one FAIL per source, like the kernel).
        self.assertEqual(source_count([_finding("a.py"), _finding("a.py"), _finding("b.py")]), 2)

    def test_empty_is_zero(self):
        self.assertEqual(source_count([]), 0)


# ---------------------------------------------------------------------------
# audit — detect + verify
# ---------------------------------------------------------------------------

class Audit(unittest.TestCase):
    def test_clean_when_no_findings(self):
        report = _audit([])
        self.assertEqual(report["verdict"], "clean")
        self.assertFalse(report["breach_confirmed"])
        self.assertEqual(report["leaking_sources"], 0)
        self.assertIsNone(report["top_leak"])

    def test_breach_when_at_or_above_fail(self):
        report = _audit([_finding("a.py")], fail_threshold=1.0)
        self.assertEqual(report["verdict"], "breach_confirmed")
        self.assertTrue(report["breach_confirmed"])

    def test_below_fail_is_clean(self):
        # fail=2 with a single leaking source -> below threshold, not confirmed.
        report = _audit([_finding("a.py")], fail_threshold=2.0)
        self.assertEqual(report["verdict"], "clean")
        self.assertFalse(report["breach_confirmed"])

    def test_top_leak_is_the_busiest_source(self):
        report = _audit([_finding("a.py"), _finding("a.py"), _finding("b.py", pattern="jwt_token")])
        self.assertEqual(report["top_leak"]["source"], "a.py")
        self.assertEqual(report["top_leak"]["finding_count"], 2)
        self.assertEqual(report["leaking_sources"], 2)

    def test_rotation_ticket_per_source_with_mandate(self):
        report = _audit([_finding("a.py"), _finding("b.py")])
        self.assertEqual(len(report["rotation_tickets"]), 2)
        self.assertIn("ROTATION REQUIRED", report["rotation_tickets"][0]["body"])
        self.assertIn("does NOT un-leak", report["rotation_tickets"][0]["body"])

    def test_dry_run_is_the_default(self):
        self.assertTrue(_audit([_finding("a.py")])["dry_run"])
        self.assertFalse(_audit([_finding("a.py")], dry_run=False)["dry_run"])

    def test_is_idempotent(self):
        findings = [_finding("a.py"), _finding("b.py"), _finding("a.py", pattern="jwt_token")]
        self.assertEqual(_audit(findings), _audit(findings))

    def test_top_leak_order_independent_on_tied_counts(self):
        a = _finding("a.py")
        b = _finding("b.py")
        self.assertEqual(_audit([a, b])["top_leak"], _audit([b, a])["top_leak"])


# ---------------------------------------------------------------------------
# redact_text — the mutation primitive (gated behind --apply in main)
# ---------------------------------------------------------------------------

class Redact(unittest.TestCase):
    def test_masks_value_and_is_idempotent(self):
        patterns = [(r"sk-ant-[A-Za-z0-9]{8,}", "anthropic_key")]
        text = 'KEY = "sk-ant-abcdef123456"'
        out, n = redact_text(text, patterns, is_placeholder=lambda v: False)
        self.assertEqual(n, 1)
        self.assertNotIn("sk-ant-abcdef123456", out)
        self.assertIn("<REDACTED:anthropic_key>", out)
        # re-running finds nothing to redact (the secret pattern no longer matches the placeholder).
        out2, n2 = redact_text(out, patterns, is_placeholder=lambda v: False)
        self.assertEqual((n2, out2), (0, out))

    def test_generic_keeps_key_name_masks_only_value(self):
        patterns = [(r"""(api_key)['"]?\s*[:=]\s*['"]([A-Za-z0-9]{20,})['"]""", "generic_hardcoded_secret")]
        text = 'api_key = "abcdefghij1234567890XY"'
        out, n = redact_text(text, patterns, is_placeholder=lambda v: False)
        self.assertEqual(n, 1)
        self.assertIn("api_key", out)
        self.assertNotIn("abcdefghij1234567890XY", out)
        self.assertIn("<REDACTED:generic_hardcoded_secret>", out)

    def test_placeholder_values_are_skipped(self):
        patterns = [(r"sk-ant-[A-Za-z0-9$\{\}]{8,}", "anthropic_key")]
        text = 'KEY = "sk-ant-${ENV_VALUE}"'
        out, n = redact_text(text, patterns, is_placeholder=lambda v: "${" in v)
        self.assertEqual((n, out), (0, text))


if __name__ == "__main__":
    unittest.main()
