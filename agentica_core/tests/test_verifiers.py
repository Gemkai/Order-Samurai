from agentica_core.doctor import run_doctor
from agentica_core.verifiers import load_verifiers, normalize_result, run_all, summarize


def _ok():
    return [{"status": "OK", "label": "a", "detail": "fine"}]


def _warn():
    return [{"status": "WARN", "label": "b", "detail": "meh"}]


def _boom():
    raise RuntimeError("kaboom")


def test_run_all_collects_and_normalizes():
    results = run_all([_ok, _warn])
    statuses = [r["status"] for r in results]
    assert statuses == ["OK", "WARN"]


def test_crashing_verifier_becomes_fail_not_abort():
    results = run_all([_ok, _boom, _warn])  # boom must not stop _warn from running
    statuses = sorted(r["status"] for r in results)
    assert statuses == ["FAIL", "OK", "WARN"]
    fail = next(r for r in results if r["status"] == "FAIL")
    assert "raised" in fail["detail"]


def test_normalize_bad_status_coerced_to_fail():
    r = normalize_result({"status": "BOGUS", "label": "x", "detail": "d"}, "src")
    assert r["status"] == "FAIL"


def test_normalize_malformed_result():
    r = normalize_result("not a dict", "src")
    assert r["status"] == "FAIL"
    assert r["label"] == "src"


def test_summarize_exit_codes():
    assert summarize(_ok())[1] == 0
    assert summarize(_warn())[1] == 0  # WARN does not fail the gate
    assert summarize([{"status": "FAIL", "label": "x", "detail": ""}])[1] == 1


def test_load_verifiers_none_when_unbound(monkeypatch):
    import agentica_core.adapter as adapter
    monkeypatch.setattr(adapter, "_load_registry", lambda: {"bare": {"runtime_root": "~"}})
    assert load_verifiers("bare") == []


def test_load_and_run_claude_real():
    verifiers = load_verifiers("claude")
    assert len(verifiers) == 4
    results = run_all(verifiers)
    assert results, "claude verifiers should produce results"
    assert all(r["status"] in ("OK", "WARN", "FAIL") for r in results)


def test_load_and_run_codex_real():
    verifiers = load_verifiers("codex")
    assert len(verifiers) == 1
    results = run_all(verifiers)
    assert results, "codex verifier should produce results"
    assert any(r["label"] == "codex-telemetry-schema" for r in results)
    assert all(r["status"] in ("OK", "WARN", "FAIL") for r in results)


def test_both_platforms_coexist_in_one_process():
    # Namespace isolation must let both `execution` packages load in one process.
    claude = run_all(load_verifiers("claude"))
    codex = run_all(load_verifiers("codex"))
    assert claude and codex
    # Re-loading claude after codex must still work (no stale `execution` namespace).
    claude_again = run_all(load_verifiers("claude"))
    assert len(claude_again) == len(claude)


def test_run_doctor_claude_returns_exit_code():
    code = run_doctor("claude")
    assert code in (0, 1)


def test_run_doctor_unknown_platform():
    assert run_doctor("nope-platform") == 2
