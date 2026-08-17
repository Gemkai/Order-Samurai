"""Eval for bin/first_blood.py -- the one-command "first blood" cost report.

Covers the pure core (build_report/render_report -- inject records in tests, same pattern
as test_secret_scrub.py) and the transcript parser (parse_transcript/scan_logs) against a
synthetic JSONL fixture shaped like a real Claude Code session transcript. Never touches
the real ~/.claude/projects tree.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]  # Order Samurai
GOV_ROOT = Path(__file__).resolve().parents[2]  # Governance (for agentica_core)
for _p in (REPO_ROOT, GOV_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bin.first_blood import (  # type: ignore[import-not-found]
    _price_for,
    _tier_for,
    build_report,
    estimate_cost,
    parse_transcript,
    scan_logs,
)


def _assistant_line(*, input_tokens=1000, output_tokens=500, cache_read=0, cache_creation=0, model="claude-sonnet-4"):
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-07-12T05:23:30.406Z",
            "sessionId": "test-session",
            "message": {
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                },
            },
        }
    )


def _record(*, total_cost=1.0, tokens_prompt=1000, tokens_completion=500, session_id="s1", status="success"):
    return {
        "total_cost": total_cost,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
        "session_id": session_id,
        "status": status,
    }


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

class EstimateCost(unittest.TestCase):
    def test_sonnet_pricing_is_nonzero_for_real_usage(self):
        cost = estimate_cost("claude-sonnet-4", 1_000_000, 1_000_000, 0)
        self.assertAlmostEqual(cost, 3.0 + 15.0, places=2)

    def test_unknown_model_falls_back_to_sonnet_price(self):
        self.assertEqual(estimate_cost("mystery-model", 1_000_000, 0, 0), estimate_cost("claude-sonnet-4", 1_000_000, 0, 0))

    def test_zero_usage_is_zero_cost(self):
        self.assertEqual(estimate_cost("claude-opus-4", 0, 0, 0), 0.0)

    def test_premium_tier_model_does_not_silently_price_at_standard_rate(self):
        # _tier_for() classifies any "mythos" model as PREMIUM (same bracket as
        # opus/fable), but _price_for() used a separate keyword list that had no
        # "mythos" entry, so it fell through to the STANDARD/sonnet default --
        # an internal contradiction between the tier label and the price used to
        # compute the very spend-spike signal this tool exists to catch.
        model = "claude-mythos-preview"
        self.assertEqual(_tier_for(model), "PREMIUM")
        self.assertNotEqual(
            _price_for(model),
            _price_for("claude-sonnet-4"),
            "a model _tier_for() grades PREMIUM must not price at the STANDARD/sonnet rate",
        )


# ---------------------------------------------------------------------------
# build_report -- pure core, injected records (no filesystem)
# ---------------------------------------------------------------------------

class BuildReport(unittest.TestCase):
    def test_empty_records_is_honest_zero_report_not_a_crash(self):
        report = build_report([])
        self.assertEqual(report["sessions_scanned"], 0)
        self.assertIsNone(report["total_cost"])
        self.assertEqual(report["spend_spikes"], [])

    def test_total_cost_sums_deduped_records(self):
        recs = [_record(total_cost=2.5, session_id="a"), _record(total_cost=1.5, session_id="b")]
        report = build_report(recs)
        self.assertEqual(report["sessions_scanned"], 2)
        self.assertEqual(report["total_cost"], 4.0)

    def test_spend_spike_flagged_at_or_above_threshold(self):
        recs = [_record(total_cost=5.0, session_id="big"), _record(total_cost=0.5, session_id="small")]
        report = build_report(recs)
        spike_ids = [s["session_id"] for s in report["spend_spikes"]]
        self.assertIn("big", spike_ids)
        self.assertNotIn("small", spike_ids)

    def test_kill_chain_findings_marked_simulated_never_faked(self):
        # ABORT-1 (identity): an unwired sub-metric must say so, never show a fabricated number.
        report = build_report([_record()])
        self.assertIn("SIMULATED", report["kill_chain_findings"])


# ---------------------------------------------------------------------------
# parse_transcript / scan_logs -- real I/O against a synthetic fixture
# ---------------------------------------------------------------------------

class ParseTranscript(unittest.TestCase):
    def test_parses_real_shaped_transcript(self):
        with TemporaryDirectory() as td:
            proj = Path(td) / "-Users-someone-myproject"
            proj.mkdir()
            transcript = proj / "abc123.jsonl"
            transcript.write_text(
                _assistant_line(input_tokens=100, output_tokens=50) + "\n"
                + _assistant_line(input_tokens=200, output_tokens=100) + "\n",
                encoding="utf-8",
            )
            rec = parse_transcript(transcript)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["tokens_prompt"], 300)
            self.assertEqual(rec["tokens_completion"], 150)
            self.assertEqual(rec["session_id"], "abc123")
            self.assertEqual(rec["project"], "myproject")
            self.assertGreater(rec["total_cost"], 0)

    def test_stub_transcript_with_no_assistant_turns_returns_none(self):
        with TemporaryDirectory() as td:
            transcript = Path(td) / "empty.jsonl"
            transcript.write_text(json.dumps({"type": "user", "timestamp": "2026-07-12T00:00:00Z"}) + "\n", encoding="utf-8")
            self.assertIsNone(parse_transcript(transcript))

    def test_cache_creation_tokens_are_not_folded_into_tokens_prompt(self):
        # cache_creation_input_tokens (prompt-cache writes) is NOT part of tokens_prompt in the
        # canonical record shape -- the SessionEnd emitter this tool's docstring claims to mirror
        # (scripts/agentica_emit.py) deliberately excludes it ("cache_write is not tracked in the
        # transcript"). Folding it into tokens_in here computes a second, divergent cost formula:
        # a session with a large cache write reports a cost far above what the live dashboard
        # would show for the same transcript.
        with TemporaryDirectory() as td:
            proj = Path(td) / "-Users-someone-myproject"
            proj.mkdir()
            transcript = proj / "abc123.jsonl"
            transcript.write_text(
                _assistant_line(input_tokens=100, output_tokens=50, cache_creation=50000) + "\n",
                encoding="utf-8",
            )
            rec = parse_transcript(transcript)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["tokens_prompt"], 100)

    def test_malformed_line_is_skipped_not_fatal(self):
        with TemporaryDirectory() as td:
            transcript = Path(td) / "mixed.jsonl"
            transcript.write_text(
                "not json at all\n" + _assistant_line(input_tokens=10, output_tokens=5) + "\n",
                encoding="utf-8",
            )
            rec = parse_transcript(transcript)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["tokens_prompt"], 10)

    def test_scan_logs_missing_dir_is_empty_not_an_error(self):
        self.assertEqual(scan_logs(Path("/no/such/dir/at/all")), [])

    def test_scan_logs_finds_project_star_star_jsonl(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            proj = root / "-Users-someone-projX"
            proj.mkdir()
            (proj / "s1.jsonl").write_text(_assistant_line() + "\n", encoding="utf-8")
            (proj / "s2.jsonl").write_text(_assistant_line() + "\n", encoding="utf-8")
            records = scan_logs(root)
            self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
