from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from agentica_core.aggregate import (  # type: ignore[attr-defined]
    REGISTRY,
    compute_metric,
    load_telemetry_records,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sandbox(test_name: str) -> Path:
    path = REPO_ROOT / ".tmp" / "test_agentica_core_aggregate" / test_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_logs(sandbox: Path, *records: dict) -> None:
    """Write records as JSON Lines into sandbox/state/logs/records.json."""
    logs_dir = sandbox / "state" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(r) for r in records)
    (logs_dir / "records.json").write_text(lines, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_telemetry_records
# ---------------------------------------------------------------------------

class LoadTelemetryRecordsTests(unittest.TestCase):

    def test_returns_empty_list_when_logs_dir_absent(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        # no state/logs/ directory created

        result = load_telemetry_records(sandbox)

        self.assertEqual(result, [])

    def test_returns_empty_list_when_logs_dir_is_empty(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        (sandbox / "state" / "logs").mkdir(parents=True, exist_ok=True)

        result = load_telemetry_records(sandbox)

        self.assertEqual(result, [])

    def test_parses_json_lines_format(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        _write_logs(sandbox, {"mcp_or_cli": "mcp"}, {"mcp_or_cli": "cli"})

        result = load_telemetry_records(sandbox)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["mcp_or_cli"], "mcp")
        self.assertEqual(result[1]["mcp_or_cli"], "cli")

    def test_parses_json_array_format(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        logs_dir = sandbox / "state" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "array.json").write_text(
            json.dumps([{"model_tier": "LOCAL"}, {"model_tier": "CLOUD"}]),
            encoding="utf-8",
        )

        result = load_telemetry_records(sandbox)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["model_tier"], "LOCAL")

    def test_skips_non_dict_lines_silently(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        logs_dir = sandbox / "state" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "mixed.json").write_text(
            '{"ok": true}\n"just a string"\n42\n{"also": "ok"}\n',
            encoding="utf-8",
        )

        result = load_telemetry_records(sandbox)

        self.assertEqual(len(result), 2)

    def test_skips_malformed_json_lines_silently(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        logs_dir = sandbox / "state" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "bad.json").write_text(
            '{"good": 1}\nnot-json-at-all\n{"good": 2}\n',
            encoding="utf-8",
        )

        result = load_telemetry_records(sandbox)

        self.assertEqual(len(result), 2)

    def test_skips_empty_files_silently(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        logs_dir = sandbox / "state" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "empty.json").write_text("", encoding="utf-8")
        (logs_dir / "real.json").write_text('{"x": 1}\n', encoding="utf-8")

        result = load_telemetry_records(sandbox)

        self.assertEqual(len(result), 1)


# ---------------------------------------------------------------------------
# compute_metric — MCP_vs_CLI_Ratio
# ---------------------------------------------------------------------------

class McpVsCliRatioTests(unittest.TestCase):

    def test_returns_zero_when_no_records_carry_the_field(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        records = [{"model_tier": "CLOUD"}, {"session_id": "abc"}]

        result = compute_metric("MCP_vs_CLI_Ratio", records, sandbox)

        self.assertTrue(result["live"])
        self.assertEqual(result["value"], 0.0)

    def test_returns_one_when_all_records_are_mcp(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        records = [{"mcp_or_cli": "mcp"}, {"mcp_or_cli": "mcp"}]

        result = compute_metric("MCP_vs_CLI_Ratio", records, sandbox)

        self.assertEqual(result["value"], 1.0)

    def test_returns_zero_when_all_records_are_cli(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        records = [{"mcp_or_cli": "cli"}, {"mcp_or_cli": "cli"}]

        result = compute_metric("MCP_vs_CLI_Ratio", records, sandbox)

        self.assertEqual(result["value"], 0.0)

    def test_returns_correct_ratio_for_mixed_records(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        records = [
            {"mcp_or_cli": "mcp"},
            {"mcp_or_cli": "cli"},
            {"mcp_or_cli": "mcp"},
            {"mcp_or_cli": "cli"},
        ]

        result = compute_metric("MCP_vs_CLI_Ratio", records, sandbox)

        self.assertAlmostEqual(result["value"], 0.5)

    def test_ignores_records_without_the_field_when_computing_ratio(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        # 2 mcp, 1 cli — the record with no field should be excluded
        records = [
            {"mcp_or_cli": "mcp"},
            {"mcp_or_cli": "mcp"},
            {"mcp_or_cli": "cli"},
            {"session_id": "no-field"},
        ]

        result = compute_metric("MCP_vs_CLI_Ratio", records, sandbox)

        self.assertAlmostEqual(result["value"], 2 / 3)


# ---------------------------------------------------------------------------
# compute_metric — Local_Routing_Share
# ---------------------------------------------------------------------------

class LocalRoutingShareTests(unittest.TestCase):

    def test_returns_zero_when_no_records_carry_model_tier(self) -> None:
        sandbox = _sandbox(self._testMethodName)

        result = compute_metric("Local_Routing_Share", [], sandbox)

        self.assertEqual(result["value"], 0.0)

    def test_returns_correct_share_for_local_records(self) -> None:
        sandbox = _sandbox(self._testMethodName)
        records = [
            {"model_tier": "LOCAL"},
            {"model_tier": "CLOUD"},
            {"model_tier": "LOCAL"},
        ]

        result = compute_metric("Local_Routing_Share", records, sandbox)

        self.assertAlmostEqual(result["value"], 2 / 3)


# ---------------------------------------------------------------------------
# compute_metric — error envelope for unknown metric
# ---------------------------------------------------------------------------

class ComputeMetricErrorEnvelopeTests(unittest.TestCase):

    def test_returns_error_envelope_for_unknown_metric_name(self) -> None:
        result = compute_metric("Nonexistent_Metric", [], REPO_ROOT)

        self.assertFalse(result["live"])
        self.assertIsNone(result["value"])
        self.assertIn("error", result)

    def test_error_envelope_names_the_unknown_metric(self) -> None:
        result = compute_metric("Ghost_Metric", [], REPO_ROOT)

        self.assertIn("Ghost_Metric", result["error"])


# ---------------------------------------------------------------------------
# compute_metric — verifier-backed metrics (smoke: real repo root)
# ---------------------------------------------------------------------------

class VerifierBackedMetricsTests(unittest.TestCase):

    def test_root_hygiene_issues_returns_live_result(self) -> None:
        result = compute_metric("Root_Hygiene_Issues", [], REPO_ROOT)

        self.assertTrue(result["live"])
        self.assertIsInstance(result["value"], int)

    def test_hardcoded_path_incidents_returns_live_result(self) -> None:
        result = compute_metric("Hardcoded_Path_Incidents", [], REPO_ROOT)

        self.assertTrue(result["live"])
        self.assertIsInstance(result["value"], int)


# ---------------------------------------------------------------------------
# REGISTRY structure
# ---------------------------------------------------------------------------

class RegistryStructureTests(unittest.TestCase):

    REQUIRED_KEYS = {"pillar", "metric", "source", "reducer", "tier"}

    def test_every_entry_has_required_keys(self) -> None:
        for entry in REGISTRY:
            with self.subTest(metric=entry.get("metric")):
                missing = self.REQUIRED_KEYS - entry.keys()
                self.assertEqual(missing, set(), f"entry missing keys: {missing}")

    def test_every_reducer_is_callable(self) -> None:
        for entry in REGISTRY:
            with self.subTest(metric=entry.get("metric")):
                self.assertTrue(callable(entry["reducer"]))

    def test_no_duplicate_metric_names(self) -> None:
        names = [e["metric"] for e in REGISTRY]
        self.assertEqual(len(names), len(set(names)))

    def test_all_pillars_are_valid(self) -> None:
        valid = {"bow", "sword", "brush", "arts"}
        for entry in REGISTRY:
            with self.subTest(metric=entry.get("metric")):
                self.assertIn(entry["pillar"], valid)


if __name__ == "__main__":
    unittest.main()
