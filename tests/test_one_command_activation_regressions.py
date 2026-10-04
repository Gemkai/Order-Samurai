"""Regression tests for the review findings on the one-command Pro install
(bin/samurai). Written by the builder after review, not test-first. No network."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_one_command_activation as base  # noqa: E402  (reuses its loaded module + fakes)

samurai = base.samurai
sandbox = base.sandbox
_clean_env = base._clean_env


class _TTYStdin:
    def isatty(self):
        return True

    def readline(self):
        raise AssertionError("must not read a terminal stdin without a prompt")


def test_install_returns_0_when_licensing_is_missing(sandbox, monkeypatch, capsys):
    def missing():
        raise SystemExit(1)  # what _licensing_module() does without agentica_core
    monkeypatch.setattr(samurai, "_licensing_module", missing)
    assert samurai.cmd_install(argparse.Namespace(no_activate=False)) == 0
    assert "activate" in capsys.readouterr().out


def test_install_returns_0_when_activation_raises(sandbox, monkeypatch):
    base._prompts(monkeypatch, base.FAKE_KEY)
    monkeypatch.setattr(base.licensing, "activate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert samurai.cmd_install(argparse.Namespace(no_activate=False)) == 0


def test_activate_prompts_hidden_when_only_stdin_is_a_terminal(sandbox, monkeypatch, capsys):
    base._providers(monkeypatch, base.VALID)
    prompts = base._prompts(monkeypatch, base.FAKE_KEY, interactive=False)
    monkeypatch.setattr(sys, "stdin", _TTYStdin())
    assert samurai.cmd_activate(base._activate_args()) == 0
    assert prompts.count == 1
    assert base.FAKE_KEY not in capsys.readouterr().out


def test_hint_is_runnable_when_samurai_is_not_on_path(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(samurai.shutil, "which", lambda _name: None)
    base._no_prompt_allowed(monkeypatch, interactive=False)
    samurai.cmd_install(argparse.Namespace(no_activate=False))
    out = capsys.readouterr().out
    import shlex
    assert f"python3 {shlex.quote(str(Path(samurai.__file__).resolve()))} activate" in out


def test_is_interactive_false_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    assert samurai._is_interactive() is False
