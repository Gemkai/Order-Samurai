#!/usr/bin/env python3
"""Asserts that version identifiers stay synchronized across all packaging mirrors."""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_versions_are_synchronized():
    root_pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    api_pkg = json.loads((REPO_ROOT / "api" / "package.json").read_text(encoding="utf-8"))
    ui_pkg = json.loads((REPO_ROOT / "dashboard-ui" / "package.json").read_text(encoding="utf-8"))

    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_version_match = re.search(r'version\s*=\s*"([^"]+)"', pyproject)
    assert pyproject_version_match, "version not found in pyproject.toml"
    pyproject_version = pyproject_version_match.group(1)

    samurai_cli = (REPO_ROOT / "bin" / "samurai").read_text(encoding="utf-8")
    cli_version_match = re.search(r'SAMURAI_VERSION\s*=\s*"([^"]+)"', samurai_cli)
    assert cli_version_match, "SAMURAI_VERSION not found in bin/samurai"
    cli_version = cli_version_match.group(1)

    expected = "1.0.2"
    assert root_pkg.get("version") == expected
    assert api_pkg.get("version") == expected
    assert ui_pkg.get("version") == expected
    assert pyproject_version == expected
    assert cli_version == expected
