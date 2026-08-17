"""Tests for execution/cli.py -- the CI gate.

The property that matters: a gate that cannot fail is not a gate. These tests inject synthetic
check families so the exit-code contract is proven against known inputs rather than against
whatever the working tree happens to look like today.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution import cli  # noqa: E402


class _Family:
    """Stand-in for a verifier module: the contract is a module-level run_checks()."""

    def __init__(self, results):
        self._results = results

    def run_checks(self):
        return self._results


class _ExplodingFamily:
    def run_checks(self):
        raise RuntimeError("verifier blew up")


def _ok(label="fine"):
    return {"status": "OK", "label": label, "detail": "all good"}


def _warn(label="iffy"):
    return {"status": "WARN", "label": label, "detail": "worth a look"}


def _fail(label="broken"):
    return {"status": "FAIL", "label": label, "detail": "policy violated"}


class TestCollect:
    def test_counts_each_status(self):
        families = [("a", _Family([_ok(), _warn(), _fail()]))]
        results, counts = cli.collect(families)
        assert counts == {"OK": 1, "WARN": 1, "FAIL": 1}
        assert len(results) == 3

    def test_tags_each_result_with_its_family(self):
        families = [("alpha", _Family([_ok()])), ("beta", _Family([_fail()]))]
        results, _ = cli.collect(families)
        assert {r["family"] for r in results} == {"alpha", "beta"}

    def test_accepts_the_name_key_used_by_root_hygiene(self):
        """Most families emit `label`; verify_agentica_root_hygiene emits `name`."""
        families = [("a", _Family([{"status": "OK", "name": "named-check", "detail": "d"}]))]
        results, _ = cli.collect(families)
        assert results[0]["label"] == "named-check"

    def test_a_crashing_verifier_becomes_a_FAIL_not_an_abort(self):
        """Swallowing a crash would turn a broken gate into a silent pass."""
        families = [("boom", _ExplodingFamily()), ("after", _Family([_ok()]))]
        results, counts = cli.collect(families)
        assert counts["FAIL"] == 1
        assert counts["OK"] == 1, "families after the crash must still run"
        crash = next(r for r in results if r["status"] == "FAIL")
        assert "verifier-crashed" in crash["label"]
        assert "RuntimeError" in crash["detail"]

    def test_unknown_status_is_treated_as_FAIL(self):
        """An unrecognised status must not silently vanish from the tally."""
        families = [("a", _Family([{"status": "MAYBE", "label": "x", "detail": ""}]))]
        _, counts = cli.collect(families)
        assert counts["FAIL"] == 1

    def test_every_family_runs_even_after_an_earlier_failure(self):
        families = [("a", _Family([_fail()])), ("b", _Family([_fail()])), ("c", _Family([_fail()]))]
        _, counts = cli.collect(families)
        assert counts["FAIL"] == 3


class TestExitCodes:
    @pytest.fixture
    def patched(self, monkeypatch):
        def _apply(families):
            monkeypatch.setattr(cli, "POLICY_FAMILIES", families)

        return _apply

    def test_clean_run_exits_zero(self, patched, capsys):
        patched([("a", _Family([_ok(), _ok()]))])
        assert cli.run_audit([]) == 0
        assert "FAIL=0" in capsys.readouterr().out

    def test_any_fail_exits_nonzero(self, patched, capsys):
        patched([("a", _Family([_ok(), _fail()]))])
        assert cli.run_audit([]) == 1
        assert "FAIL=1" in capsys.readouterr().out

    def test_warn_alone_does_not_fail_the_gate_by_default(self, patched):
        patched([("a", _Family([_warn()]))])
        assert cli.run_audit([]) == 0

    def test_warn_as_error_promotes_warn_to_failure(self, patched):
        patched([("a", _Family([_warn()]))])
        assert cli.run_audit(["--warn-as-error"]) == 1

    def test_warn_as_error_still_passes_a_fully_clean_run(self, patched):
        patched([("a", _Family([_ok()]))])
        assert cli.run_audit(["--warn-as-error"]) == 0


class TestOutput:
    @pytest.fixture
    def patched(self, monkeypatch):
        monkeypatch.setattr(cli, "POLICY_FAMILIES", [("a", _Family([_ok("good"), _fail("bad")]))])

    def test_json_format_is_parseable_and_carries_both_keys(self, patched, capsys):
        import json

        cli.run_audit(["--format", "json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["FAIL"] == 1
        assert {r["label"] for r in payload["results"]} == {"good", "bad"}

    def test_quiet_prints_failures_and_summary_but_not_passes(self, patched, capsys):
        cli.run_audit(["--quiet"])
        out = capsys.readouterr().out
        assert "bad" in out
        assert "good" not in out
        assert "Summary:" in out

    def test_default_text_output_lists_every_check(self, patched, capsys):
        cli.run_audit([])
        out = capsys.readouterr().out
        assert "good" in out and "bad" in out


class TestMain:
    def test_version_subcommand(self, capsys):
        assert cli.main(["version"]) == 0
        assert "order-samurai" in capsys.readouterr().out

    def test_no_command_prints_usage_and_signals_misuse(self, capsys):
        assert cli.main([]) == 2
        assert "usage: order-samurai" in capsys.readouterr().out

    def test_help_exits_zero(self, capsys):
        assert cli.main(["--help"]) == 0
        assert "audit" in capsys.readouterr().out

    def test_usage_points_at_doctor_for_runtime_health(self, capsys):
        """The audit/doctor split is deliberate; the CLI has to say so."""
        cli.main(["--help"])
        assert "doctor.py" in capsys.readouterr().out


class TestRealPolicyFamilies:
    def test_the_shipped_families_all_expose_run_checks(self):
        """Guards the composition contract: a renamed function would break the gate silently."""
        assert cli.POLICY_FAMILIES, "the gate must run at least one family"
        for name, module in cli.POLICY_FAMILIES:
            assert callable(getattr(module, "run_checks", None)), f"{name} lost run_checks()"

    def test_runtime_health_families_are_excluded_from_the_gate(self):
        """Live payload, telemetry, and daemon checks would false-fail or stall in CI."""
        names = {name for name, _ in cli.POLICY_FAMILIES}
        assert "live-sources" not in names
        assert "telemetry-freshness" not in names
        assert "local-llm" not in names
        assert "exec-chain" not in names


class TestAuditRequiresACheckout:
    """`pip install` ships the verifiers but not the config/ contracts they enforce.

    Measured on a real install: the console script resolved its root to
    site-packages and printed `Summary: OK=2 WARN=0 FAIL=3` -- a verdict about its
    own install directory, indistinguishable from a verdict about a repository.
    """

    def _hide_policy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "ROOT_HYGIENE_POLICY_PATH",
                            tmp_path / "site-packages" / "config" / "root_hygiene_policy.json")

    def test_audit_exits_two_when_the_policy_contracts_are_absent(self, monkeypatch, tmp_path):
        self._hide_policy(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)

        assert cli.run_audit([]) == 2

    def test_audit_emits_no_verdict_when_it_cannot_see_its_policy(self, monkeypatch, tmp_path, capsys):
        """The regression itself: no Summary line, and nothing on stdout a CI step
        could mistake for a result. The explanation goes to stderr."""
        self._hide_policy(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)

        cli.run_audit([])

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Summary:" not in captured.err
        assert "cannot audit" in captured.err

    def test_json_format_also_refuses_rather_than_emitting_an_empty_result(self, monkeypatch, tmp_path, capsys):
        """A machine reader must not receive a well-formed zero-finding payload."""
        self._hide_policy(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)

        assert cli.run_audit(["--format", "json"]) == 2
        assert capsys.readouterr().out == ""

    def test_the_message_names_a_checkout_found_above_the_working_directory(
        self, monkeypatch, tmp_path, capsys
    ):
        checkout = tmp_path / "order-samurai"
        (checkout / "config").mkdir(parents=True)
        (checkout / "config" / "root_hygiene_policy.json").write_text("{}", encoding="utf-8")
        workdir = checkout / "execution" / "nested"
        workdir.mkdir(parents=True)
        self._hide_policy(monkeypatch, tmp_path)
        monkeypatch.chdir(workdir)

        cli.run_audit([])

        assert str(checkout) in capsys.readouterr().err

    def test_the_message_says_so_when_no_checkout_is_above_the_working_directory(
        self, monkeypatch, tmp_path, capsys
    ):
        bare = tmp_path / "somewhere-else"
        bare.mkdir()
        self._hide_policy(monkeypatch, tmp_path)
        monkeypatch.chdir(bare)

        cli.run_audit([])

        assert "No checkout was found" in capsys.readouterr().err

    def test_a_real_checkout_still_audits(self, capsys):
        """The guard must not fire in the tree this suite runs from."""
        assert cli.policy_contracts_unavailable() is None

    def test_version_and_help_work_without_the_policy_contracts(self, monkeypatch, tmp_path, capsys):
        """Only `audit` needs the contracts; the other commands must stay usable."""
        self._hide_policy(monkeypatch, tmp_path)

        assert cli.main(["version"]) == 0
        assert cli.main(["--help"]) == 0

    def test_find_pack_root_returns_none_outside_a_pack(self, tmp_path):
        assert cli.find_pack_root(tmp_path) is None
