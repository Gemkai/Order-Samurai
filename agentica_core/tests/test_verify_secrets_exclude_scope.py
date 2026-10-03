"""verify_secrets.scan_path must apply EXCLUDE_DIRS to directories INSIDE the scanned
root, not to the ancestors of the root.

Order Samurai remediation runs execute from `.tmp/worktrees/<...>/`; matching against the
absolute path parts made every file under such a checkout look excluded, so the scan
returned "no hardcoded secrets" without reading anything.
"""
from __future__ import annotations

from agentica_core import verify_secrets

# Built at runtime so this file's own source holds no contiguous secret.
_ANT_KEY = "sk-ant-" + "a" * 28


def test_scan_finds_secret_when_root_lives_under_an_excluded_named_directory(tmp_path):
    root = tmp_path / ".tmp" / "worktrees" / "wt"
    root.mkdir(parents=True)
    (root / "leak.py").write_text(f'token = "{_ANT_KEY}"\n', encoding="utf-8")

    findings = verify_secrets.scan_path(root)

    assert "anthropic_key" in {f["pattern_name"] for f in findings}


def test_scan_still_skips_excluded_directory_inside_the_root(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "leak.py").write_text(f'token = "{_ANT_KEY}"\n', encoding="utf-8")

    assert verify_secrets.scan_path(tmp_path) == []
