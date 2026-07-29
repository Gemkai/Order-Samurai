"""Unit tests for execution/audit_remediation_patch.run_static_checks.

The static security checks must judge what a patch ADDS — a remediation patch
whose whole purpose is to remove an insecure pattern must not be rejected for
containing that pattern on its removed/context lines.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.audit_remediation_patch import run_static_checks


def _diff(*body: str) -> str:
    return "\n".join(["--- a/bin/run.js", "+++ b/bin/run.js", "@@ -1,3 +1,3 @@", *body]) + "\n"


def test_patch_removing_shell_true_is_not_flagged():
    patch = _diff(
        "-cp.spawn('ls', {shell: true});",
        "+cp.spawn('ls', {shell: false});",
    )
    assert run_static_checks(patch) == []


def test_patch_adding_shell_true_is_flagged():
    patch = _diff("+cp.spawn('ls', {shell: true});")
    assert any("shell: true" in f for f in run_static_checks(patch))


def test_cors_wildcard_on_context_line_is_not_flagged():
    patch = _diff(
        " app.use(cors());",
        "+app.use(helmet());",
    )
    assert run_static_checks(patch) == []


def test_cors_wildcard_added_is_flagged():
    patch = _diff("+app.use(cors());")
    assert any("CORS" in f for f in run_static_checks(patch))


def test_patch_removing_spawn_concatenation_is_not_flagged():
    patch = _diff("-cp.spawn('convert ' + userInput);")
    assert run_static_checks(patch) == []


def test_non_diff_content_still_scanned_wholesale():
    raw = "cp.spawn('ls', {shell: true});\n"
    assert any("shell: true" in f for f in run_static_checks(raw))


def test_env_file_header_check_still_sees_diff_headers():
    patch = "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n+SECRET=1\n"
    assert any(".env" in f for f in run_static_checks(patch))


def test_env_example_template_is_not_flagged():
    # .env.example is the committed template (governance_review's documented
    # "copy .env.example to .env" flow) — documenting a new var there is a
    # legitimate remediation, not a secrets edit.
    patch = "--- a/.env.example\n+++ b/.env.example\n@@ -1 +1,2 @@\n CODEX_KEY=\n+NEW_SERVICE_URL=\n"
    assert run_static_checks(patch) == []


def test_credentials_doc_is_not_flagged():
    patch = ("--- a/docs/credentials-rotation.md\n+++ b/docs/credentials-rotation.md\n"
             "@@ -1 +1,2 @@\n # Rotation\n+New step\n")
    assert run_static_checks(patch) == []


def test_real_credentials_file_still_flagged():
    patch = '--- a/config/credentials.json\n+++ b/config/credentials.json\n@@ -1 +1 @@\n+{"key": "v"}\n'
    assert any("credentials" in f for f in run_static_checks(patch))


def test_env_local_still_flagged():
    patch = "--- a/.env.local\n+++ b/.env.local\n@@ -1 +1 @@\n+SECRET=1\n"
    assert any(".env" in f for f in run_static_checks(patch))
