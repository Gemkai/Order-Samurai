"""Acceptance tests for one-command Pro activation (bin/samurai).

A. `samurai activate` takes the key optionally: positional, SAMURAI_LICENSE_KEY,
   hidden prompt (interactive), or one line on stdin (non-interactive).
B. `samurai install` offers Pro activation at its end (hidden prompt, env key,
   skip switches, retry on invalid key) and always returns 0.

Test seams on bin/samurai: `_read_hidden(prompt_text)` and `_is_interactive()`.
No real network: Gumroad and Lemon Squeezy provider functions are patched.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _layout import governance_root  # noqa: E402

ROOT = governance_root(__file__)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agentica_core.licensing as licensing  # noqa: E402
import execution.gumroad_mcp as gumroad_mcp  # noqa: E402
import execution.lemonsqueezy_mcp as lemonsqueezy_mcp  # noqa: E402

_SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
_loader = SourceFileLoader("samurai_one_command", str(_SAMURAI_PATH))
_spec = importlib.util.spec_from_loader("samurai_one_command", _loader)
assert _spec
samurai = importlib.util.module_from_spec(_spec)
sys.modules["samurai_one_command"] = samurai
_loader.exec_module(samurai)

FAKE_KEY = "FAKE-TEST-KEY-0000-1111-ABCD"
FAKE_EMAIL = "buyer@example.com"
MASK = "****ABCD"

VALID = {"valid": True, "status": "active", "refunded": False, "customer_email": FAKE_EMAIL}
INVALID = {"valid": False, "not_found": True, "error": "license key not recognized by Gumroad"}
UNREACHABLE = {"valid": False, "error": "Could not reach Gumroad (timed out). Check your connection."}
REFUNDED = {"valid": True, "refunded": True, "status": "refunded"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

class Provider:
    """Fake Gumroad. `answers` is a list of validate results consumed in order
    (the last one repeats). Counts validate and activate calls."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.validate_calls = 0
        self.activate_calls = 0

    def validate(self, key, *_a, **_k):
        i = min(self.validate_calls, len(self.answers) - 1)
        self.validate_calls += 1
        return dict(self.answers[i])

    def activate(self, key, instance_name, *_a, **_k):
        self.activate_calls += 1
        return {"activated": True, "instance_id": "gum_test", "instance_name": instance_name,
                "license_key": key, "customer_email": FAKE_EMAIL}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SAMURAI_LICENSE_KEY", raising=False)
    monkeypatch.delenv("SAMURAI_NO_PROMPT", raising=False)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path / ".samurai"))
    return tmp_path


def _providers(monkeypatch, *answers) -> Provider:
    p = Provider(answers or [VALID])
    monkeypatch.setattr(gumroad_mcp, "validate_license_key", p.validate)
    monkeypatch.setattr(gumroad_mcp, "activate_license_key", p.activate)
    # Legacy fallback must never reach the network.
    monkeypatch.setattr(lemonsqueezy_mcp, "validate_license_key",
                        lambda *a, **k: {"valid": False, "error": "not recognized"})
    monkeypatch.setattr(lemonsqueezy_mcp, "activate_license_key",
                        lambda *a, **k: {"activated": False, "error": "unused"})
    return p


class Prompts:
    """Fake _read_hidden: returns scripted answers, records prompt count."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.count = 0

    def __call__(self, prompt_text=""):
        self.count += 1
        if not self.answers:
            return ""
        return self.answers.pop(0)


def _prompts(monkeypatch, *answers, interactive=True) -> Prompts:
    pr = Prompts(answers)
    monkeypatch.setattr(samurai, "_read_hidden", pr)
    monkeypatch.setattr(samurai, "_is_interactive", lambda: interactive)
    return pr


def _no_prompt_allowed(monkeypatch, interactive=True):
    def boom(*_a, **_k):
        raise AssertionError("_read_hidden must not be called")
    monkeypatch.setattr(samurai, "_read_hidden", boom)
    monkeypatch.setattr(samurai, "_is_interactive", lambda: interactive)


def _activate_args(key=None, instance_name=None):
    return argparse.Namespace(license_key=key, instance_name=instance_name)


def _install_args(no_activate=False):
    return argparse.Namespace(no_activate=no_activate)


def _out(capsys) -> str:
    c = capsys.readouterr()
    return c.out + c.err


def _write_pro_license(tmp_path):
    home = tmp_path / ".samurai"
    home.mkdir(parents=True, exist_ok=True)
    (home / "license.json").write_text(json.dumps({
        "tier": "pro", "valid": True, "status": "active", "simulated": True, "provider": "dev",
    }))


# --------------------------------------------------------------------------- #
# A. samurai activate
# --------------------------------------------------------------------------- #

def test_activate_positional_key_still_works(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_activate(_activate_args(FAKE_KEY)) == 0
    assert p.activate_calls == 1
    assert licensing.is_pro() is True


def test_activate_reads_env_var_without_prompting(sandbox, monkeypatch, capsys):
    monkeypatch.setenv("SAMURAI_LICENSE_KEY", FAKE_KEY)
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_activate(_activate_args()) == 0
    assert p.activate_calls == 1
    assert licensing.is_pro() is True


def test_activate_interactive_prompts_hidden_and_strips_whitespace(sandbox, monkeypatch, capsys):
    seen = []
    p = _providers(monkeypatch)
    monkeypatch.setattr(gumroad_mcp, "validate_license_key",
                        lambda key, *a, **k: (seen.append(key), dict(VALID))[1])
    pr = _prompts(monkeypatch, f"  {FAKE_KEY}\n ")
    assert samurai.cmd_activate(_activate_args()) == 0
    assert pr.count == 1
    assert seen and all(k == FAKE_KEY for k in seen)
    assert p.activate_calls == 1


def test_activate_non_interactive_reads_one_line_from_stdin(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch, interactive=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(FAKE_KEY + "\n"))
    assert samurai.cmd_activate(_activate_args()) == 0
    assert p.activate_calls == 1
    assert licensing.is_pro() is True


def test_activate_stdin_without_trailing_newline_and_with_spaces(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch, interactive=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"  {FAKE_KEY}  "))
    assert samurai.cmd_activate(_activate_args()) == 0
    assert p.activate_calls == 1


@pytest.mark.parametrize("source", ["prompt", "stdin", "env", "positional"])
def test_activate_empty_key_exits_1_and_never_calls_provider(sandbox, monkeypatch, capsys, source):
    p = _providers(monkeypatch)
    key = None
    if source == "prompt":
        _prompts(monkeypatch, "   ")
    else:
        _no_prompt_allowed(monkeypatch, interactive=False)
    if source == "stdin":
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    elif source == "env":
        monkeypatch.setenv("SAMURAI_LICENSE_KEY", "   ")
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    elif source == "positional":
        key = "  "
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert samurai.cmd_activate(_activate_args(key)) == 1
    assert "no license key" in _out(capsys).lower()
    assert p.validate_calls == 0 and p.activate_calls == 0


def test_activate_success_masks_key_in_output(sandbox, monkeypatch, capsys):
    _providers(monkeypatch)
    _prompts(monkeypatch, FAKE_KEY)
    assert samurai.cmd_activate(_activate_args()) == 0
    out = _out(capsys)
    assert FAKE_KEY not in out
    assert MASK in out


def test_activate_failure_never_prints_full_key(sandbox, monkeypatch, capsys):
    _providers(monkeypatch, INVALID)
    _prompts(monkeypatch, FAKE_KEY)
    assert samurai.cmd_activate(_activate_args()) == 1
    assert FAKE_KEY not in _out(capsys)


def test_activate_positional_argument_is_optional_in_argparse(monkeypatch):
    captured = {}

    def fake(args):
        captured["args"] = args
        return 0

    monkeypatch.setattr(samurai, "cmd_activate", fake)
    monkeypatch.setattr(sys, "argv", ["samurai", "activate"])
    with pytest.raises(SystemExit) as exc:
        samurai.main()
    assert exc.value.code == 0, "`samurai activate` with no key must parse"
    assert captured["args"].license_key is None


# --------------------------------------------------------------------------- #
# B. samurai install offers Pro activation
# --------------------------------------------------------------------------- #

def test_install_with_env_key_activates_non_interactively(sandbox, monkeypatch, capsys):
    monkeypatch.setenv("SAMURAI_LICENSE_KEY", FAKE_KEY)
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys)
    assert p.activate_calls == 1
    assert "Pro activated" in out
    assert licensing.is_pro() is True
    assert (sandbox / "home" / ".claude" / "settings.json").exists()


def test_install_already_pro_makes_no_prompt_or_provider_call(sandbox, monkeypatch, capsys):
    _write_pro_license(sandbox)
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys).lower()
    assert p.validate_calls == 0 and p.activate_calls == 0
    assert "already" in out and "pro" in out


def test_install_env_no_prompt_skips_activation(sandbox, monkeypatch, capsys):
    monkeypatch.setenv("SAMURAI_NO_PROMPT", "1")
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_install(_install_args()) == 0
    assert p.validate_calls == 0 and p.activate_calls == 0
    assert licensing.is_pro() is False


def test_install_no_activate_flag_skips_activation(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch)
    assert samurai.cmd_install(_install_args(no_activate=True)) == 0
    assert p.validate_calls == 0 and p.activate_calls == 0
    assert licensing.is_pro() is False


def test_install_non_interactive_prints_activate_hint_only(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    _no_prompt_allowed(monkeypatch, interactive=False)
    assert samurai.cmd_install(_install_args()) == 0
    assert "samurai activate" in _out(capsys)
    assert p.validate_calls == 0 and p.activate_calls == 0


def test_install_interactive_enter_stays_free_with_upgrade_hint(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch)
    pr = _prompts(monkeypatch, "")
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys)
    assert pr.count == 1
    assert p.validate_calls == 0 and p.activate_calls == 0
    assert "free" in out.lower()
    assert "samurai activate" in out
    assert licensing.is_pro() is False


def test_install_interactive_valid_key_activates_pro(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch, VALID)
    pr = _prompts(monkeypatch, FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys)
    assert pr.count == 1
    assert p.activate_calls == 1
    assert "Pro activated" in out
    assert MASK in out
    assert licensing.is_pro() is True


def test_install_invalid_then_invalid_then_valid_reprompts(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch, INVALID, INVALID, VALID)
    pr = _prompts(monkeypatch, "BAD-ONE-0001", "BAD-TWO-0002", FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    assert pr.count == 3
    assert p.activate_calls == 1
    assert licensing.is_pro() is True


def test_install_three_invalid_keys_stop_after_three_prompts(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch, INVALID)
    pr = _prompts(monkeypatch, "BAD-ONE-0001", "BAD-TWO-0002", "BAD-THREE-0003", FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys)
    assert pr.count == 3
    assert p.activate_calls == 0
    assert licensing.is_pro() is False
    assert "samurai activate" in out


def test_install_unreachable_gumroad_does_not_reprompt(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch, UNREACHABLE)
    pr = _prompts(monkeypatch, FAKE_KEY, FAKE_KEY, FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    assert pr.count == 1
    assert "samurai activate" in _out(capsys)
    assert p.activate_calls == 0
    assert licensing.is_pro() is False


def test_install_refunded_key_does_not_reprompt_and_stays_free(sandbox, monkeypatch, capsys):
    p = _providers(monkeypatch, REFUNDED)
    pr = _prompts(monkeypatch, FAKE_KEY, FAKE_KEY, FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    assert pr.count == 1
    assert p.activate_calls == 0
    assert licensing.is_pro() is False


@pytest.mark.parametrize("scenario", ["env", "valid", "invalid", "unreachable", "refunded"])
def test_install_never_prints_full_key_or_email(sandbox, monkeypatch, capsys, scenario):
    if scenario == "env":
        monkeypatch.setenv("SAMURAI_LICENSE_KEY", FAKE_KEY)
        _providers(monkeypatch, VALID)
        _no_prompt_allowed(monkeypatch)
    else:
        answer = {"valid": VALID, "invalid": INVALID, "unreachable": UNREACHABLE,
                  "refunded": REFUNDED}[scenario]
        _providers(monkeypatch, answer)
        _prompts(monkeypatch, FAKE_KEY, FAKE_KEY, FAKE_KEY)
    assert samurai.cmd_install(_install_args()) == 0
    out = _out(capsys)
    assert FAKE_KEY not in out
    assert FAKE_EMAIL not in out
    if scenario in ("env", "valid"):
        assert "Pro activated" in out  # guard: the privacy check ran on a real activation
