"""
Tests for Order Samurai Installer & CLI Tool (bin/samurai)
Verifies:
- samurai install registers hooks & creates backup
- samurai doctor evaluates system checks correctly
- samurai uninstall restores prior settings & performs zero-residue cleanup
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

def test_installer_lifecycle(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    
    samurai_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("SAMURAI_ROOT", str(samurai_root))

    samurai_bin = samurai_root / "bin" / "samurai"

    # Pre-create the config Claude Code actually loads: ~/.claude/settings.json.
    # Until v1.0.1 this test targeted ~/.claude/hooks/settings.json and asserted
    # a {"name": ...} entry shape -- neither of which Claude Code reads, which is
    # how v1.0.0 shipped a guard that never fired. See tests/test_hook_wiring.py.
    claude_dir = home_dir / ".claude"
    claude_dir.mkdir(parents=True)
    settings_file = claude_dir / "settings.json"
    initial_content = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other_hook"}]}
    ]}}
    with open(settings_file, "w") as f:
        json.dump(initial_content, f)

    # 1. Run samurai install
    res = subprocess.run([sys.executable, str(samurai_bin), "install"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Installation complete" in res.stdout

    # Verify settings.json merged, in Claude Code's own schema
    with open(settings_file) as f:
        data = json.load(f)

    def commands(event):
        return [
            h.get("command", "")
            for entry in data["hooks"][event]
            for h in entry.get("hooks", []) or []
        ]

    assert any("prompt_injection_guard" in c for c in commands("PreToolUse"))
    assert any("secret_scrubber_realtime" in c for c in commands("PostToolUse"))
    assert any("other_hook" in c for c in commands("PreToolUse"))

    # Verify backup exists (labelled per target so uninstall can't cross-restore)
    backups_dir = home_dir / ".samurai" / "backups"
    assert backups_dir.exists()
    assert len(list(backups_dir.glob("claude-settings.bak.*"))) >= 1

    # 2. Run samurai doctor
    res_doc = subprocess.run([sys.executable, str(samurai_bin), "doctor"], capture_output=True, text=True)
    assert "Order Samurai Doctor" in res_doc.stdout
    assert "Claude Code Hook Registration" in res_doc.stdout

    # 3. Run samurai uninstall (zero residue audit)
    res_un = subprocess.run([sys.executable, str(samurai_bin), "uninstall"], capture_output=True, text=True)
    assert res_un.returncode == 0
    assert "uninstalled cleanly" in res_un.stdout

    # Verify samurai hooks removed, user's own hook preserved
    with open(settings_file) as f:
        un_data = json.load(f)
    un_cmds = [
        h.get("command", "")
        for entry in un_data.get("hooks", {}).get("PreToolUse", [])
        for h in entry.get("hooks", []) or []
    ]
    assert not any("prompt_injection_guard" in c for c in un_cmds)
    assert any("other_hook" in c for c in un_cmds), "uninstall clobbered the user's own hook"
    # Verify zero-residue: ~/.samurai state removed
    assert not (home_dir / ".samurai").exists()
