"""Pro entitlement authority (agentica_core/licensing.py) — fail-closed to Free.

No network: activate() is exercised with the simulated SAMURAI-PRO-KEY path so tests
never hit Lemon Squeezy. Every test isolates ~/.samurai via SAMURAI_HOME.
"""
import json

import agentica_core.licensing as lic


def _home(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    return tmp_path


def test_is_pro_false_without_license(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    assert lic.is_pro() is False
    assert lic.status()["tier"] == "free"


def test_activate_simulated_key_then_is_pro(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    res = lic.activate("SAMURAI-PRO-KEY-TEST", instance_name="ci-box")
    assert res["ok"] is True
    assert lic.is_pro() is True
    st = lic.status()
    assert st["tier"] == "pro" and st["activated"] is True
    # key is masked, never echoed in full
    assert st["license_key"].startswith("****")


def test_activate_rejects_unknown_key(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    res = lic.activate("TOTALLY-BOGUS", instance_name="ci-box")
    assert res["ok"] is False
    assert lic.is_pro() is False


def test_refunded_license_is_not_pro(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    lic.activate("SAMURAI-PRO-KEY-TEST", instance_name="ci-box")
    # simulate a post-refund entitlement on disk
    p = lic.license_path()
    ent = json.loads(p.read_text())
    ent["status"] = "refunded"
    ent["refunded"] = True
    p.write_text(json.dumps(ent))
    assert lic.is_pro() is False


def test_deactivate_reverts_to_free(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    lic.activate("SAMURAI-PRO-KEY-TEST", instance_name="ci-box")
    assert lic.is_pro() is True
    res = lic.deactivate()
    assert res["ok"] is True
    assert lic.is_pro() is False
    # idempotent
    assert lic.deactivate()["ok"] is True


def test_malformed_license_fails_closed(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    lic.license_path().parent.mkdir(parents=True, exist_ok=True)
    lic.license_path().write_text("{ not json")
    assert lic.is_pro() is False
