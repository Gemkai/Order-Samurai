import json

from agentica_core import calibrate, insights


def _hist(tmp_path, rows):
    p = tmp_path / "hist.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_calibrate_lower_metric_percentiles(tmp_path, monkeypatch):
    # Token_Execution_Density is a plain lower-is-better metric -> warn=p75, fail=p95
    # Use MIN_POINTS (8) rows so calibration is not skipped due to thin history.
    vals = [100, 150, 200, 250, 300, 350, 400, 500]
    rows = [{"week": f"w{i}", "values": {"brush/Token Efficiency/Token_Execution_Density": v}}
            for i, v in enumerate(vals)]
    monkeypatch.setattr(calibrate, "THRESHOLDS_PATH", tmp_path / "thresholds.json")
    out = calibrate.calibrate(store=_hist(tmp_path, rows))
    assert "Token_Execution_Density" in out
    t = out["Token_Execution_Density"]
    assert t["warn"] < t["fail"] and t["n"] == 8


def test_calibrate_skips_per_session_and_thin(tmp_path, monkeypatch):
    rows = [{"week": f"w{i}", "values": {"arts/Interaction/Frustration_Signals": 10,
                                         "bow/Activity/Error_Rate": 1}} for i in range(5)]
    monkeypatch.setattr(calibrate, "THRESHOLDS_PATH", tmp_path / "thresholds.json")
    out = calibrate.calibrate(store=_hist(tmp_path, rows))
    assert "Frustration_Signals" not in out   # per:session -> skipped
    assert "Error_Rate" not in out            # zero spread -> skipped


def test_overlay_applies_and_falls_back():
    # overlay never breaks: with no thresholds file it's a no-op; manual defaults intact
    assert insights.METRIC_RULES["Vulnerability_MTTR"]["dir"] == "lower"
    assert "warn" in insights.METRIC_RULES["Vulnerability_MTTR"]


def test_calibration_only_tightens_never_loosens():
    # dir:lower — a looser (higher) calibrated warn/fail is capped at the manual ceiling
    assert insights._clamp_threshold("lower", 40000, 80000, 299002, 518575) == (40000, 80000)
    # dir:lower — a tighter (lower) calibrated value passes through
    assert insights._clamp_threshold("lower", 40000, 80000, 30000, 70000) == (30000, 70000)
    # dir:higher — a looser (lower) calibrated value is floored at the manual value
    assert insights._clamp_threshold("higher", 25, 10, 22.14, 1.5) == (25, 10)
    # dir:higher — a stricter (higher) calibrated value passes through
    assert insights._clamp_threshold("higher", 25, 10, 30, 15) == (30, 15)
