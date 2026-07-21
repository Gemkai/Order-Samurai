"""Drift guard: bin/secret_scrub.py must stay in lockstep with the kernel.

Lives under agentica_core/tests (not Order Samurai/tests) so `agentica_core` resolves to the
canonical Governance package. The bin is loaded by explicit file path (stdlib-only at import).

Secrets_Detected = sum(1 for r in verify_secrets.run_checks() if FAIL) = the count of source
files with >=1 finding. The mechanism re-measures the same number from raw scan findings, so this
asserts (1) bin.source_count(scan findings) == the kernel's run_checks FAIL count on planted
secrets, and (2) the bin's live FAIL threshold == the (post-calibration) METRIC_CONFIG value.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from agentica_core import verify_secrets
from agentica_core.insights import METRIC_CONFIG

_BIN_PATH = Path(__file__).resolve().parents[2] / "bin" / "secret_scrub.py"

# Build planted secrets at runtime (concatenation) so THIS file's own source never contains a
# contiguous secret — test_secrets.py::test_scanner_does_not_flag_its_own_patterns scans the
# agentica_core package (which holds this file) and must stay clean.
_ANT_KEY = "sk-ant-" + "a" * 28
_GEM_KEY = "AIzaSy" + "A" * 33


def _load_bin():
    spec = importlib.util.spec_from_file_location("secret_scrub", _BIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_count_matches_kernel_fail_count(tmp_path):
    bin_mod = _load_bin()
    (tmp_path / "f1.py").write_text(f'token = "{_ANT_KEY}"\n', encoding="utf-8")
    (tmp_path / "f2.py").write_text(f'key = "{_GEM_KEY}"\n', encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")

    kernel_fails = sum(1 for r in verify_secrets.run_checks([tmp_path]) if r["status"] == "FAIL")
    findings = verify_secrets.scan_path(tmp_path)
    assert bin_mod.source_count(findings) == kernel_fails == 2


def test_clean_tree_is_zero_both_sides(tmp_path):
    bin_mod = _load_bin()
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    kernel_fails = sum(1 for r in verify_secrets.run_checks([tmp_path]) if r["status"] == "FAIL")
    assert bin_mod.source_count(verify_secrets.scan_path(tmp_path)) == kernel_fails == 0


def test_fail_threshold_matches_metric_config():
    bin_mod = _load_bin()
    assert bin_mod._live_fail_threshold() == float(METRIC_CONFIG["Secrets_Detected"]["fail"])


def test_redaction_uses_kernel_patterns_and_drops_the_finding(tmp_path):
    # --apply path parity: redacting with the kernel's own SECRET_PATTERNS must make a re-scan clean.
    bin_mod = _load_bin()
    f = tmp_path / "leak.py"
    f.write_text(f'token = "{_ANT_KEY}"\n', encoding="utf-8")
    assert verify_secrets.scan_path(tmp_path)  # leaks before
    new_text, n = bin_mod.redact_text(f.read_text(encoding="utf-8"),
                                      verify_secrets.SECRET_PATTERNS, verify_secrets._is_placeholder)
    f.write_text(new_text, encoding="utf-8")
    assert n >= 1
    assert verify_secrets.scan_path(tmp_path) == []  # clean after — the metric would drop to 0
