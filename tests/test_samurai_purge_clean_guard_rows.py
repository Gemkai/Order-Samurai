"""`samurai install` purges the plaintext "Clean" rows older guards logged.

Before the guard fix, every clean tool call was appended to
state/kill_chain_unmatched.jsonl with the first 200 chars of raw tool input
(Bash commands, Write/Edit content -- keys, tokens). The fix stops new rows,
but the zip never ships these logs, so an upgrade leaves the old rows in
place -- and log rotation copies them into state/logs/rotated/. install.sh
runs `samurai install` on every install and upgrade, so the purge lives there.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

SAMURAI_PATH = Path(__file__).resolve().parents[1] / "bin" / "samurai"
LOADER = SourceFileLoader("samurai_purge_clean_rows", str(SAMURAI_PATH))
SPEC = importlib.util.spec_from_loader("samurai_purge_clean_rows", LOADER)
samurai_cli = importlib.util.module_from_spec(SPEC)
sys.modules["samurai_purge_clean_rows"] = samurai_cli
LOADER.exec_module(samurai_cli)

REPO_ROOT = SAMURAI_PATH.parents[1]
_redact = samurai_cli._load_guard_redactor(REPO_ROOT)

# Built at runtime so the repo's own secret scanners and injection guards never
# see a key or block-phrase literal in this file.
BLOCK_WORDS = "da" + "n mode"
FAKE_KEY = "sk-" + "ant-" + "api03-" + "Zq7Rm4Tn8Wp2" * 4

HEADER = "# kill_chain_unmatched schema v1\n"
CLEAN = json.dumps({"ts": "2026-10-01T00:00:00Z", "event_type": "prompt_injection",
                    "detail": f"curl -H 'x-api-key: {FAKE_KEY}'",
                    "source": "prompt_injection_guard: Clean",
                    "remediation_action": "logged", "confidence": 0.0}) + "\n"
SUSPICIOUS = json.dumps({"ts": "2026-10-02T00:00:00Z", "event_type": "prompt_injection",
                         "detail": "act as root",
                         "source": "prompt_injection_guard: Suspicious pattern '\\bact as\\b' matched but Semantic check denied",
                         "remediation_action": "logged", "confidence": 0.5}) + "\n"
UNPARSEABLE = "{not json\n"


def _seed(state: Path) -> tuple[Path, Path]:
    live = state / "kill_chain_unmatched.jsonl"
    archive = state / "logs" / "rotated" / "kill_chain_unmatched-rotated-2026-09-01.jsonl"
    archive.parent.mkdir(parents=True)
    live.write_text(HEADER + CLEAN + SUSPICIOUS + CLEAN + UNPARSEABLE, encoding="utf-8")
    archive.write_text(CLEAN + SUSPICIOUS, encoding="utf-8")
    return live, archive


def test_purge_removes_clean_rows_from_live_log_and_rotated_archives(tmp_path):
    live, archive = _seed(tmp_path)
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (3, 0)
    assert live.read_text(encoding="utf-8") == HEADER + SUSPICIOUS + UNPARSEABLE
    assert archive.read_text(encoding="utf-8") == SUSPICIOUS


def test_purge_is_idempotent_and_leaves_clean_files_untouched(tmp_path):
    live, _ = _seed(tmp_path)
    samurai_cli._clean_guard_rows(tmp_path, _redact)
    before = live.stat().st_mtime_ns
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert live.stat().st_mtime_ns == before


def test_purge_preserves_file_mode(tmp_path):
    live, _ = _seed(tmp_path)
    live.chmod(0o600)
    samurai_cli._clean_guard_rows(tmp_path, _redact)
    assert live.stat().st_mode & 0o777 == 0o600


def test_purge_with_no_logs_is_a_noop(tmp_path):
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert list(tmp_path.iterdir()) == []


def test_install_purges_clean_rows(tmp_path):
    root = tmp_path / "install"
    (root / "state").mkdir(parents=True)
    live, archive = _seed(root / "state")
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "SAMURAI_ROOT": str(root)}
    res = subprocess.run([sys.executable, str(SAMURAI_PATH), "install"], input="",
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "Installation complete" in res.stdout
    assert "Removed 3" in res.stdout
    for path in (live, archive):
        assert FAKE_KEY not in path.read_text(encoding="utf-8")
    assert SUSPICIOUS in live.read_text(encoding="utf-8")


def test_only_exact_clean_source_is_removed(tmp_path):
    """A row merely mentioning the Clean source string is not a Clean row."""
    decoy = json.dumps({"ts": "2026-10-03T00:00:00Z", "event_type": "prompt_injection",
                        "detail": "grep 'prompt_injection_guard: Clean' state/*.jsonl --override",
                        "source": "prompt_injection_guard: Suspicious pattern '\\boverride\\b' matched",
                        "confidence": 0.5}) + "\n"
    live = tmp_path / "kill_chain_unmatched.jsonl"
    live.write_text(CLEAN + decoy, encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (1, 0)
    assert live.read_text(encoding="utf-8") == decoy


def test_pathological_line_is_kept_and_does_not_stop_the_purge(tmp_path):
    deep = "[" * 100_000 + "\n"  # json.loads raises RecursionError, not ValueError
    live = tmp_path / "kill_chain_unmatched.jsonl"
    live.write_text(CLEAN + deep + SUSPICIOUS, encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (1, 0)
    assert live.read_text(encoding="utf-8") == deep + SUSPICIOUS


def test_symlinked_log_is_left_alone(tmp_path):
    """os.replace would swap the link for a file and leave the target's rows behind."""
    target = tmp_path / "elsewhere.jsonl"
    target.write_text(CLEAN, encoding="utf-8")
    (tmp_path / "kill_chain_unmatched.jsonl").symlink_to(target)
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert (tmp_path / "kill_chain_unmatched.jsonl").is_symlink()


def test_install_survives_a_failing_purge(tmp_path, monkeypatch, capsys):
    import argparse
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SAMURAI_ROOT", str(tmp_path / "install"))
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path / "home" / ".samurai"))

    def boom(*_args):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(samurai_cli, "_clean_guard_rows", boom)
    monkeypatch.setattr(samurai_cli, "_offer_pro_activation", lambda _args: None)
    assert samurai_cli.cmd_install(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "Could not clean old kill-chain log rows" in out
    assert "Installation complete" in out



def _row(source: str, detail: str, confidence: float, **extra) -> str:
    return json.dumps({"ts": "2026-09-20T00:00:00Z", "event_type": "prompt_injection",
                       "detail": detail, "source": source,
                       "remediation_action": "logged", "confidence": confidence, **extra}) + "\n"


SUSP_SOURCE = "prompt_injection_guard: Suspicious pattern '\\bact as\\b' matched but Semantic check denied"
BLOCK_SOURCE = f"prompt_injection_guard: Pattern matched: {BLOCK_WORDS}"
SCRUBBER = json.dumps({"ts": "2026-09-20T00:00:00Z", "event_type": "model_exfiltration",
                       "detail": "Matched exfil patterns: anthropic_key",
                       "source": "secret_scrubber_realtime: Bash (pre-block)", "chain_id": 7}) + "\n"


def test_old_suspicious_and_blocked_rows_are_redacted(tmp_path):
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    events = tmp_path / "kill_chain_events.jsonl"
    archive = tmp_path / "logs" / "rotated" / "kill_chain_events-rotated-2026-09-01.jsonl"
    archive.parent.mkdir(parents=True)
    unmatched.write_text(_row(SUSP_SOURCE, f"act as root; export KEY={FAKE_KEY}", 0.5), encoding="utf-8")
    events.write_text(_row(BLOCK_SOURCE, f"{BLOCK_WORDS} {FAKE_KEY}", 1.0, chain_id=13) + SCRUBBER,
                      encoding="utf-8")
    archive.write_text(_row(BLOCK_SOURCE, f"{BLOCK_WORDS} {FAKE_KEY}", 1.0, chain_id=13), encoding="utf-8")

    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 3)
    for path in (unmatched, events, archive):
        assert FAKE_KEY not in path.read_text(encoding="utf-8")
    susp = json.loads(unmatched.read_text(encoding="utf-8"))
    assert susp["detail"] == "act as root; export KEY=[REDACTED:anthropic_key]"
    assert susp["confidence"] == 0.5 and susp["source"] == SUSP_SOURCE
    blocked, scrubber = events.read_text(encoding="utf-8").splitlines(keepends=True)
    assert json.loads(blocked)["chain_id"] == 13
    assert scrubber == SCRUBBER  # other producers' rows are untouched, byte for byte


def test_key_fragment_cut_at_200_chars_is_masked(tmp_path):
    """Old guards truncated before redacting, so a key could end mid-way, too short to match."""
    prefix = "act as admin " + "a" * (200 - len("act as admin ") - 21)
    detail = prefix + " " + FAKE_KEY[:20]
    assert len(detail) == 200
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    unmatched.write_text(_row(SUSP_SOURCE, detail, 0.5), encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 1)
    text = unmatched.read_text(encoding="utf-8")
    assert FAKE_KEY[:20] not in text
    assert "[REDACTED:truncated_token]" in text


def test_redaction_is_idempotent(tmp_path):
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    unmatched.write_text(_row(SUSP_SOURCE, f"act as root postgres://u:{FAKE_KEY}@db Bearer {FAKE_KEY}", 0.5),
                         encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 1)
    before = (unmatched.read_bytes(), unmatched.stat().st_mtime_ns)
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert (unmatched.read_bytes(), unmatched.stat().st_mtime_ns) == before


def test_detail_withheld_when_redaction_unavailable(tmp_path):
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    unmatched.write_text(_row(SUSP_SOURCE, f"act as root {FAKE_KEY}", 0.5), encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, lambda _text: None) == (0, 1)
    detail = json.loads(unmatched.read_text(encoding="utf-8"))["detail"]
    assert detail.startswith("[withheld:")


def test_install_redacts_old_alert_rows(tmp_path):
    root = tmp_path / "install"
    state = root / "state"
    state.mkdir(parents=True)
    (state / "kill_chain_events.jsonl").write_text(
        _row(BLOCK_SOURCE, f"{BLOCK_WORDS} {FAKE_KEY}", 1.0, chain_id=13), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "SAMURAI_ROOT": str(root)}
    res = subprocess.run([sys.executable, str(SAMURAI_PATH), "install"], input="",
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr
    assert "Redacted secrets in 1 older" in res.stdout
    assert FAKE_KEY not in (state / "kill_chain_events.jsonl").read_text(encoding="utf-8")


def test_rows_that_grow_past_200_on_redaction_settle_in_one_pass(tmp_path):
    """The 200-char fragment rule applies to raw old details, not ones redaction lengthened."""
    head = "act as admin; KEY=abcdef "
    # Spaced filler: a 32+ char run would be masked as long_token and shrink the row.
    detail = head + ("bb " * 100)[:199 - len(head) - 15] + " " + "c" * 14
    assert len(detail) == 199
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    unmatched.write_text(_row(SUSP_SOURCE, detail, 0.5), encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 1)
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert "[REDACTED:truncated_token]" not in unmatched.read_text(encoding="utf-8")


def test_already_redacted_200_char_row_is_left_unchanged(tmp_path):
    """Rows the fixed guard wrote (already masked) are never rewritten by install."""
    head = "act as root KEY=[REDACTED:assignment] cat "
    detail = head + ("/Users/someone/project/src/" * 10)[:200 - len(head)]
    assert len(detail) == 200
    line = _row(SUSP_SOURCE, detail, 0.5)
    unmatched = tmp_path / "kill_chain_unmatched.jsonl"
    unmatched.write_text(line, encoding="utf-8")
    assert samurai_cli._clean_guard_rows(tmp_path, _redact) == (0, 0)
    assert unmatched.read_text(encoding="utf-8") == line


def test_loading_the_redactor_leaves_sys_path_unchanged():
    before = list(sys.path)
    samurai_cli._load_guard_redactor(REPO_ROOT)
    assert sys.path == before
