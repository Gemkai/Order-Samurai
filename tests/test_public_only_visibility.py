"""Public-only outside loads must degrade visibly or preserve their output."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import ModuleType
from uuid import uuid4

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Dotenv writes directly to os.environ; restore those writes as well.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("SAMURAI_ROOT", str(tmp_path))
    monkeypatch.setenv("ORDER_SAMURAI_ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setitem(sys.modules, "jsonl_append", None)

    def no_network(*args, **kwargs):
        raise AssertionError("Public-only visibility tests must stay offline")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


def test_state_report_vault_absent_is_visible(tmp_path, monkeypatch):
    from agentica_core import state_report

    monkeypatch.setattr(state_report, "_ROOT", tmp_path)
    monkeypatch.setattr(state_report.verify_layers, "run_checks", lambda: [])
    monkeypatch.setattr(state_report.verify_secrets, "run_checks", lambda: [])
    monkeypatch.setattr(state_report, "_platform_rows", lambda: [])

    assert state_report._load_vault_health() is None
    assert state_report._vault_snapshot() == {"available": False}
    report = state_report.build_report(payload={"pillars": {}}, timestamp="fixed")
    assert "- Vault health script unavailable." in report.splitlines()

    script = tmp_path / "Knowledge" / "vault" / "_scripts" / "vault_health.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "def check_raw_pending(): return ['pending']\n"
        "def wiki_article_counts(): return {'filled': 3, 'empty': 0}\n"
        "def find_stale_articles(): return ['stale']\n"
        "def find_orphaned_wiki(): return ['orphan', 'other']\n"
        "def compute_score(pending, counts, stale, orphans): return 73\n",
        encoding="utf-8",
    )
    loaded = state_report._load_vault_health()
    assert Path(loaded.__file__) == script
    assert state_report._vault_snapshot() == {
        "available": True,
        "pending": 1,
        "articles": 3,
        "empty_domains": ["empty"],
        "stale": 1,
        "orphans": 2,
        "score": 73,
    }
    report = state_report.build_report(payload={"pillars": {}}, timestamp="fixed")
    assert "- Score: 73/100." in report.splitlines()
    assert "Vault health script unavailable." not in report


def test_prompt_injection_guard_fallback_is_equivalent(tmp_path, monkeypatch):
    fallback = _load_module("bin/prompt_injection_guard.py", monkeypatch)
    assert fallback._append_jsonl.__module__ == fallback.__name__
    entry = {"message": "café 雪\nsecond line", "count": 2, "blocked": True}
    path = tmp_path / "missing" / "nested" / "events.jsonl"
    assert not path.parent.exists()
    assert fallback._append_jsonl(path, entry) is True
    expected = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    assert path.read_bytes() == expected
    assert [json.loads(line) for line in path.read_bytes().splitlines()] == [entry]

    reference = _reference_appender(tmp_path, monkeypatch)
    with_helper = _load_module("bin/prompt_injection_guard.py", monkeypatch)
    assert with_helper.__name__ != fallback.__name__
    assert with_helper._append_jsonl is reference.append_jsonl

    prefix = b'{"existing": true}\n'
    path.write_bytes(prefix)
    assert with_helper._append_jsonl(path, entry) is True
    helper_bytes = path.read_bytes()
    assert helper_bytes == prefix + expected
    path.write_bytes(prefix)
    assert fallback._append_jsonl(path, entry) is True
    assert path.read_bytes() == helper_bytes

    # Opening a directory for append fails even when tests run as root.
    assert fallback._append_jsonl(tmp_path, entry) is False


def test_secret_scrubber_fallback_is_equivalent_log_pre_event(tmp_path, monkeypatch):
    scrubber = _scrubber(tmp_path, monkeypatch)
    _reference_appender(tmp_path, monkeypatch)
    prefix = b'{"existing": true}\n'
    scrubber.DATA.mkdir(parents=True)
    scrubber.LOG.write_bytes(prefix)
    scrubber._log_pre_event("Bash", ["internal_ip", "café"], "block")
    reference = _rows_without_time(scrubber.LOG)
    assert reference == [
        {"existing": True},
        {"mode": "pre", "tool": "Bash", "labels": ["internal_ip", "café"],
         "action": "block"},
    ]

    scrubber.LOG.write_bytes(prefix)
    monkeypatch.setitem(sys.modules, "jsonl_append", None)
    scrubber._log_pre_event("Bash", ["internal_ip", "café"], "block")
    assert _rows_without_time(scrubber.LOG) == reference


def test_secret_scrubber_fallback_is_equivalent_emit_chain14_pre(tmp_path, monkeypatch):
    scrubber = _scrubber(tmp_path, monkeypatch)
    _reference_appender(tmp_path, monkeypatch)
    path = tmp_path / "state" / "kill_chain_events.jsonl"
    prefix = b'{"existing": true}\n'
    path.parent.mkdir(parents=True)
    path.write_bytes(prefix)
    scrubber._emit_chain14_pre("WebFetch", "internal_ip, café", "block")
    reference = _rows_without_time(path)
    assert reference == [
        {"existing": True},
        {"chain_id": 14, "event_type": "model_exfiltration",
         "detail": "Matched exfil patterns: internal_ip, café",
         "source": "secret_scrubber_realtime: WebFetch (pre-block)",
         "remediation_action": "block", "confidence": 1.0},
    ]

    path.write_bytes(prefix)
    monkeypatch.setitem(sys.modules, "jsonl_append", None)
    scrubber._emit_chain14_pre("WebFetch", "internal_ip, café", "block")
    assert _rows_without_time(path) == reference


def test_repo_auditor_dotenv_fallback(tmp_path, monkeypatch):
    _dotenv_file(tmp_path, monkeypatch)
    auditor = _load_module(
        "execution/repo_auditor.py", monkeypatch,
        file_path=tmp_path / "execution" / "repo_auditor.py",
    )
    _assert_dotenv_values()
    monkeypatch.delenv("KEY")
    monkeypatch.delenv("QUOTED")
    auditor._load_dotenv()
    _assert_dotenv_values()


def test_governance_review_dotenv_fallback(tmp_path, monkeypatch):
    env_path = _dotenv_file(tmp_path, monkeypatch)
    review = _load_module(
        "governance_review.py", monkeypatch,
        file_path=tmp_path / "governance_review.py",
    )
    review._load_dotenv(env_path)
    _assert_dotenv_values()


def test_dashboard_shot_fails_loudly_without_playwright(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy, sys\n"
         "sys.modules['playwright'] = None\n"
         "sys.modules['playwright.sync_api'] = None\n"
         "runpy.run_path(sys.argv[1], run_name='__main__')\n",
         str(REPO / "dashboard-ui" / "_shot.py")],
        cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr or "ImportError" in result.stderr
    assert "playwright" in result.stderr
    assert not list(tmp_path.glob("*.png"))


def _load_module(relative_path, monkeypatch, *, file_path=None) -> ModuleType:
    name = f"_w0pub_{Path(relative_path).stem}_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, REPO / relative_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    if file_path is not None:
        # Redirect import-time dotenv lookup without changing the loaded source.
        monkeypatch.setattr(module, "__file__", str(file_path))
    spec.loader.exec_module(module)
    return module


def _reference_appender(tmp_path, monkeypatch) -> ModuleType:
    scripts = tmp_path / ".claude" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    helper = scripts / "jsonl_append.py"
    helper.write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "def append_jsonl(file_path, entry):\n"
        "    path = Path(file_path)\n"
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    data = (json.dumps(entry, ensure_ascii=False) + '\\n').encode('utf-8')\n"
        "    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)\n"
        "    try:\n"
        "        return os.write(fd, data) == len(data)\n"
        "    finally:\n"
        "        os.close(fd)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.delitem(sys.modules, "jsonl_append")
    module = importlib.import_module("jsonl_append")
    assert Path(module.__file__) == helper
    return module


def _scrubber(tmp_path, monkeypatch) -> ModuleType:
    module = _load_module("bin/secret_scrubber_realtime.py", monkeypatch)
    monkeypatch.setattr(module, "DATA", tmp_path / "data")
    monkeypatch.setattr(module, "LOG", tmp_path / "data" / "scrubber.jsonl")
    monkeypatch.setattr(module, "_REPO_ROOT", tmp_path)
    return module


def _rows_without_time(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row.pop("timestamp", None)
        row.pop("ts", None)
    return rows


def _dotenv_file(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)
    for key in ("KEY", "QUOTED", "# COMMENT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("EXISTING", "environment wins")
    path = tmp_path / ".env"
    path.write_text(
        '# COMMENT=ignored\n\nKEY=value\nQUOTED="v"\nEXISTING=file value\n',
        encoding="utf-8",
    )
    return path


def _assert_dotenv_values():
    assert os.environ["KEY"] == "value"
    assert os.environ["QUOTED"] == "v"
    assert os.environ["EXISTING"] == "environment wins"
    assert "# COMMENT" not in os.environ
