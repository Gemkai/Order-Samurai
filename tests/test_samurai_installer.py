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

    # Pre-create initial claude settings.json
    claude_hooks_dir = home_dir / ".claude" / "hooks"
    claude_hooks_dir.mkdir(parents=True)
    settings_file = claude_hooks_dir / "settings.json"
    initial_content = {"hooks": {"PreToolUse": [{"name": "other_hook", "command": "echo 1"}]}}
    with open(settings_file, "w") as f:
        json.dump(initial_content, f)

    # 1. Run samurai install
    res = subprocess.run([sys.executable, str(samurai_bin), "install"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "Installation complete" in res.stdout

    # Verify settings.json merged
    with open(settings_file) as f:
        data = json.load(f)
    pre = data["hooks"]["PreToolUse"]
    post = data["hooks"]["PostToolUse"]
    assert any(h.get("name") == "samurai_prompt_injection_guard" for h in pre)
    assert any(h.get("name") == "samurai_secret_scrubber" for h in post)
    assert any(h.get("name") == "other_hook" for h in pre)

    # Verify backup exists
    backups_dir = home_dir / ".samurai" / "backups"
    assert backups_dir.exists()
    assert len(list(backups_dir.glob("settings.json.bak.*"))) >= 1

    # 2. Run samurai doctor
    res_doc = subprocess.run([sys.executable, str(samurai_bin), "doctor"], capture_output=True, text=True)
    assert "Order Samurai Doctor" in res_doc.stdout
    assert "Claude Code Hook Registration" in res_doc.stdout

    # 3. Run samurai uninstall (zero residue audit)
    res_un = subprocess.run([sys.executable, str(samurai_bin), "uninstall"], capture_output=True, text=True)
    assert res_un.returncode == 0
    assert "uninstalled cleanly" in res_un.stdout

    # Verify samurai hooks removed
    with open(settings_file) as f:
        un_data = json.load(f)
    assert not any(h.get("name") == "samurai_prompt_injection_guard" for h in un_data.get("hooks", {}).get("PreToolUse", []))
    # Verify zero-residue: ~/.samurai state removed
    assert not (home_dir / ".samurai").exists()
