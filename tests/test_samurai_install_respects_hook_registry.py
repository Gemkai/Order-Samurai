"""Public-repo build: `samurai install` must not write Claude Code's settings when the hook registry exists.

When ~/.claude/scripts/hook_registry.py is present the dispatcher already covers the
guard (prompt-injection-guard) and the scrubber (secret-scrubber-realtime), so a direct
registration duplicates them (remediation T7a, finding B2). The agent-agnostic
~/.samurai/settings.json record is still written. With no registry, behaviour is
unchanged. Runs the real CLI (`bin/samurai install`) in a subprocess with a temporary
HOME, SAMURAI_HOME and SAMURAI_ROOT.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

SAMURAI = Path(__file__).resolve().parents[1] / "bin" / "samurai"
# Public-product layout: hook scripts live under SAMURAI_ROOT/bin and Claude Code's
# real config is ~/.claude/settings.json. install is non-interactive via --no-activate.
HOOK_SUBDIR = "bin"
CLAUDE_SETTINGS_REL = Path(".claude") / "settings.json"
INSTALL_ARGS = ["--no-activate"]
SEEDED_CLAUDE_FILES = (CLAUDE_SETTINGS_REL,)
PRIOR = {"theme": "dark", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
    {"type": "command", "command": "python3 /opt/user_hook.py"}]}]}}


REGISTRY_IDS = (
    'HOOKS = {\n'
    '    "prompt-injection-guard": {"script": "hooks/prompt_injection_guard.py", "event": "PreToolUse"},\n'
    '    "secret-scrubber-realtime": {"script": "scripts/secret_scrubber_realtime.py", "event": "PostToolUse"},\n'
    '}\n'
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install(tmp_path: Path, with_registry: bool, seed: bool, registry_text: str = REGISTRY_IDS):
    home = tmp_path / "home"
    root = tmp_path / "root"
    (root / HOOK_SUBDIR).mkdir(parents=True)
    for name in ("prompt_injection_guard.py", "secret_scrubber_realtime.py"):
        (root / HOOK_SUBDIR / name).write_text("")
    (home / ".claude" / "scripts").mkdir(parents=True)
    if with_registry:
        (home / ".claude" / "scripts" / "hook_registry.py").write_text(registry_text)
    if seed:
        for rel in SEEDED_CLAUDE_FILES:
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(json.dumps(PRIOR, indent=2))
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOME": str(home / "codex-state"),
           "SAMURAI_CODEX_APP_BIN": str(home / "absent-app" / "codex"), "HOME": str(home), "SAMURAI_HOME": str(home / ".samurai"),
           "SAMURAI_ROOT": str(root), "SAMURAI_NO_PROMPT": "1"}
    proc = subprocess.run([sys.executable, str(SAMURAI), "install", *INSTALL_ARGS],
                          env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    return home, proc


def test_registry_present_leaves_claude_settings_byte_identical(tmp_path):
    home, proc = _install(tmp_path, with_registry=True, seed=True)
    # _install seeds before running, so re-seeding identical bytes gives the "before" hash.
    expected = hashlib.sha256(json.dumps(PRIOR, indent=2).encode()).hexdigest()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for rel in SEEDED_CLAUDE_FILES:
        assert _sha(home / rel) == expected, rel
    assert "dispatcher already covers" in proc.stdout
    record = json.loads((home / ".samurai" / "settings.json").read_text())
    commands = json.dumps(record["hooks"])
    assert "prompt_injection_guard" in commands and "secret_scrubber_realtime" in commands


def test_registry_present_does_not_create_claude_settings(tmp_path):
    home, proc = _install(tmp_path, with_registry=True, seed=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (home / CLAUDE_SETTINGS_REL).exists()


def test_no_registry_still_registers_both_hooks(tmp_path):
    home, proc = _install(tmp_path, with_registry=False, seed=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cfg = json.loads((home / CLAUDE_SETTINGS_REL).read_text())
    assert cfg["theme"] == "dark"
    pre = json.dumps(cfg["hooks"]["PreToolUse"])
    post = json.dumps(cfg["hooks"]["PostToolUse"])
    assert "prompt_injection_guard" in pre and "/opt/user_hook.py" in pre
    assert "secret_scrubber_realtime" in post
    assert "dispatcher already covers" not in proc.stdout


# --- samurai doctor on a registry machine -----------------------------------------------------
IDS_ONLY_IN_COMMENT = '# "prompt-injection-guard" "secret-scrubber-realtime"\nHOOKS = {}\n'


def _packaged_root(root: Path) -> None:
    # Doctor probes the packaged guard with a blocking payload, so a stand-in empty script
    # would (correctly) fail as "not protecting". Use the real shipped scripts.
    repo = SAMURAI.parents[1]
    for name in (HOOK_SUBDIR, "agentica_core"):
        shutil.copytree(repo / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))


def _doctor(tmp_path: Path, registry_text: str | None, scrubber_body: str = "") -> str:
    home = tmp_path / "home"
    root = tmp_path / "root"
    _packaged_root(root)
    if scrubber_body:
        (root / HOOK_SUBDIR / "secret_scrubber_realtime.py").write_text(scrubber_body)
    (home / ".claude" / "scripts").mkdir(parents=True)
    if registry_text is not None:
        (home / ".claude" / "scripts" / "hook_registry.py").write_text(registry_text)
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOME": str(home / "codex-state"),
           "SAMURAI_CODEX_APP_BIN": str(home / "absent-app" / "codex"), "HOME": str(home), "SAMURAI_HOME": str(home / ".samurai"),
           "SAMURAI_ROOT": str(root), "SAMURAI_NO_PROMPT": "1"}
    proc = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=env, capture_output=True,
                          text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    return proc.stdout + proc.stderr


def _line(output: str, check_name: str) -> str:
    lines = [ln for ln in output.splitlines() if check_name in ln]
    assert len(lines) == 1, output
    return lines[0]


def _doctor_line(tmp_path: Path, registry_text: str | None) -> str:
    return _line(_doctor(tmp_path, registry_text), "Claude Code Hook Registration")


def test_doctor_passes_hook_check_when_registry_dispatcher_covers_hooks(tmp_path):
    assert "PASS" in _doctor_line(tmp_path, REGISTRY_IDS)


def test_doctor_still_fails_when_registry_lacks_the_hooks(tmp_path):
    assert "FAIL" in _doctor_line(tmp_path, "HOOKS = {}\n")


def test_doctor_still_fails_with_no_registry_and_no_registration(tmp_path):
    assert "FAIL" in _doctor_line(tmp_path, None)


def test_doctor_fails_when_hook_ids_appear_only_in_a_comment(tmp_path):
    assert "FAIL" in _doctor_line(tmp_path, IDS_ONLY_IN_COMMENT)


def test_doctor_fails_when_registry_is_not_valid_python(tmp_path):
    assert "FAIL" in _doctor_line(tmp_path, '"prompt-injection-guard" "secret-scrubber-realtime" {{{')


# Check 5 (Hook Execution) must run the bundled scripts when the dispatcher runs them, since
# nothing is registered in the Claude settings to probe.
def test_doctor_hook_execution_probes_bundled_scripts_under_the_dispatcher(tmp_path):
    out = _doctor(tmp_path, REGISTRY_IDS)
    assert "PASS" in _line(out, "Hook Execution"), out


def test_doctor_hook_execution_fails_when_a_bundled_script_crashes_under_the_dispatcher(tmp_path):
    out = _doctor(tmp_path, REGISTRY_IDS, scrubber_body="raise SystemExit(3)\n")
    line = _line(out, "Hook Execution")
    assert "FAIL" in line and "secret_scrubber_realtime" in line, out


def test_registry_without_both_hooks_still_registers_directly(tmp_path):
    # A registry that does not dispatch both hooks must not leave them registered nowhere.
    home, proc = _install(tmp_path, with_registry=True, seed=True, registry_text="HOOKS = {}\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cfg = json.loads((home / CLAUDE_SETTINGS_REL).read_text())
    assert "prompt_injection_guard" in json.dumps(cfg["hooks"]["PreToolUse"])
    assert "secret_scrubber_realtime" in json.dumps(cfg["hooks"]["PostToolUse"])
    assert "dispatcher already covers" not in proc.stdout


def test_doctor_fails_when_the_guard_is_registered_on_the_wrong_event(tmp_path):
    wrong = REGISTRY_IDS.replace('"script": "hooks/prompt_injection_guard.py", "event": "PreToolUse"',
                                 '"script": "hooks/prompt_injection_guard.py", "event": "PostToolUse"')
    assert wrong != REGISTRY_IDS
    assert "FAIL" in _doctor_line(tmp_path, wrong)


def test_doctor_flags_direct_registration_plus_registry_as_a_double_run(tmp_path):
    home = tmp_path / "home"
    root = tmp_path / "root"
    _packaged_root(root)
    guard = root / HOOK_SUBDIR / "prompt_injection_guard.py"
    (home / ".claude" / "scripts").mkdir(parents=True)
    (home / ".claude" / "scripts" / "hook_registry.py").write_text(REGISTRY_IDS)
    (home / CLAUDE_SETTINGS_REL).write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": f"python3 {guard}"}]}]}}))
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOME": str(home / "codex-state"),
           "SAMURAI_CODEX_APP_BIN": str(home / "absent-app" / "codex"), "HOME": str(home), "SAMURAI_HOME": str(home / ".samurai"),
           "SAMURAI_ROOT": str(root), "SAMURAI_NO_PROMPT": "1"}
    proc = subprocess.run([sys.executable, str(SAMURAI), "doctor"], env=env, capture_output=True,
                          text=True, timeout=60, stdin=subprocess.DEVNULL, check=False)
    line = _line(proc.stdout + proc.stderr, "Claude Code Hook Registration")
    assert "FAIL" in line and "twice" in line, proc.stdout
