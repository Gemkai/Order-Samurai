import json

from agentica_core import verify_secrets as vs


def test_scan_detects_gemini_key():
    fake = "AIzaSy" + "A" * 33
    findings = vs.scan_text(f'key = "{fake}"', "t.py")
    assert any(f["pattern_name"] == "google_gemini_key" for f in findings)


def test_scan_detects_generic_secret():
    findings = vs.scan_text('api_key = "' + "k" * 40 + '"', "t.py")
    assert any(f["pattern_name"] == "generic_hardcoded_secret" for f in findings)


def test_scan_skips_placeholder_values():
    fake = "AIzaSy" + ("example" + "A" * 26)  # 33-char tail containing a placeholder marker
    assert vs.scan_text(f'key = "{fake}"', "t.py") == []


def test_mask_hides_middle_and_full_value():
    secret = "AIzaSy" + "B" * 33
    masked = vs._mask(secret)
    assert secret not in masked
    assert masked.startswith("AIza")
    assert masked.endswith(secret[-4:])


def test_run_checks_flags_planted_secret(tmp_path):
    (tmp_path / "leak.py").write_text(f'TOKEN = "{"sk-ant-" + "z" * 30}"\n', encoding="utf-8")
    results = vs.run_checks(roots=[tmp_path])
    assert any(r["status"] == "FAIL" for r in results)
    assert any("leak.py" in r["detail"] for r in results)


def test_run_checks_clean_dir_is_ok(tmp_path):
    (tmp_path / "fine.py").write_text("x = 1  # nothing secret here\n", encoding="utf-8")
    results = vs.run_checks(roots=[tmp_path])
    assert len(results) == 1 and results[0]["status"] == "OK"


def test_scanner_does_not_flag_its_own_patterns(tmp_path):
    # pointing the scanner at the agentica_core package must not match the regex definitions
    import agentica_core
    pkg = next(iter(agentica_core.__path__))
    from pathlib import Path
    results = vs.run_checks(roots=[Path(pkg)])
    assert all(r["status"] == "OK" for r in results)


def test_write_log_canonical_shape(tmp_path):
    target = tmp_path / "security_gate_log.jsonl"
    findings = [{"pattern_name": "google_gemini_key", "match_masked": "AIza****wxyz", "source": "x.py"}]
    vs.write_log(findings, exit_code=2, path=target, timestamp="2026-01-01T00:00:00+00:00")
    event = json.loads(target.read_text(encoding="utf-8").strip())
    assert event["finding_count"] == 1
    assert event["exit_code"] == 2
    assert event["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert event["findings"][0]["pattern_name"] == "google_gemini_key"


def test_control_plane_baseline_is_clean():
    # the real default roots (governance config/code + Data) must have no leaked secrets
    results = vs.run_checks()
    assert all(r["status"] != "FAIL" for r in results)
