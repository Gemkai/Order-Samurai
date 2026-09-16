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


def test_patch_removing_windows_path_is_not_flagged():
    # Check 5's Windows-drive-letter regex scanned raw patch_content wholesale,
    # unlike its own Unix-abspath half a few lines below (already scoped to
    # ADDED source-code lines) — so a remediation patch whose only change is
    # DELETING a hardcoded Windows path (verify_no_stale_paths.py's
    # STALE_LITERALS actively hunts for exactly this) was rejected for
    # containing the very literal it exists to remove.
    patch = _diff("-const p = 'C:\\Users\\example\\Desktop';")
    assert run_static_checks(patch) == []


def test_patch_adding_windows_path_is_still_flagged():
    patch = _diff("+const p = 'C:\\Users\\example\\Desktop';")
    assert any("absolute paths" in f.lower() for f in run_static_checks(patch))


def test_non_diff_windows_path_still_scanned_wholesale():
    raw = "const p = 'C:\\Users\\example\\Desktop';\n"
    assert any("absolute paths" in f.lower() for f in run_static_checks(raw))


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


def test_credentials_file_under_space_containing_dir_still_flagged():
    # Regression: the gitignore/credentials check used a `\S+` regex on the diff
    # header to pull the target path, which cannot match a space. This repo's own
    # "Order Samurai" subtree has a literal space, so `+++ b/Governance/Order
    # Samurai/config/credentials.json` truncated to "Governance/Order" -> basename
    # "order", silently defeating the check for every credentials/.env file under
    # a space-containing directory (check_path_scope has no credentials pattern,
    # so nothing else catches this).
    patch = ('--- a/Governance/Order Samurai/config/credentials.json\n'
             '+++ b/Governance/Order Samurai/config/credentials.json\n'
             '@@ -1 +1 @@\n+{"key": "v"}\n')
    assert any("credentials" in f for f in run_static_checks(patch))


def test_env_local_still_flagged():
    patch = "--- a/.env.local\n+++ b/.env.local\n@@ -1 +1 @@\n+SECRET=1\n"
    assert any(".env" in f for f in run_static_checks(patch))


def test_spawn_concatenation_behind_nested_call_is_still_flagged():
    # Check 2's regex `spawn\([^)]*(\+|\$\{)` cannot span past ANY ")" —
    # including one belonging to a nested call inside the spawn() argument
    # list. A patch adding `spawn(resolveBinary(), [..., userInput + ext])`
    # is genuine CWE-88 concatenation risk, but the regex engine hits the ")"
    # closing `resolveBinary()` before it can reach the "+", so re.search
    # finds no match and this deterministic gate silently passes it through.
    patch = _diff(
        "+cp.spawn(resolveBinary(), ['--path', userInput + ext]);"
    )
    assert any("CLI argument injection" in f for f in run_static_checks(patch))


def test_added_line_starting_with_double_plus_is_not_dropped_as_a_fake_header():
    # A real "+++ file" header always has a trailing space ("+++ b/path"). An
    # added line whose own CONTENT starts with "++" (e.g. a pre-increment or,
    # here, an inline comment marker) renders as "+++..." (no space) after the
    # diff's own "+" prefix — `_added_code()`'s bare `not l.startswith("+++")`
    # guard mistook that for the header line and silently dropped the whole
    # line from every check it feeds (CORS, CLI injection, debug handlers,
    # shell:true), even though it is real added code.
    patch = _diff("+++x; cp.exec('ls', {shell: true});")
    assert any("shell: true" in f for f in run_static_checks(patch))


def test_added_line_starting_with_double_plus_still_triggers_abspath_check():
    # Same defect as above, in the sibling classifier `_added_code_lines()`
    # that backs check 9 (hardcoded absolute paths).
    patch = ("--- a/script.py\n+++ b/script.py\n@@ -1 +1 @@\n"
             "+++x = '/Users/exampleuser/file'\n")
    assert any("absolute paths" in f.lower() for f in run_static_checks(patch))


def test_debug_handler_leak_not_silenced_by_unrelated_file_mentioning_node_env():
    # Check 4's `not "NODE_ENV" in patch_content` scans the ENTIRE multi-file
    # patch, not just the file containing the ungated console.error/log call.
    # An unrelated file elsewhere in the same patch that merely mentions
    # NODE_ENV (a genuine gate for a DIFFERENT concern) silences the check for
    # every other file, even one whose stack-trace leak has no gate at all.
    patch = (
        "--- a/bin/run.js\n+++ b/bin/run.js\n@@ -1,1 +1,1 @@\n"
        "+console.error(err.stack);\n"
        "--- a/config/settings.js\n+++ b/config/settings.js\n@@ -1,1 +1,1 @@\n"
        "+if (process.env.NODE_ENV === 'production') { /* unrelated gate */ }\n"
    )
    assert any("Debug handlers" in f for f in run_static_checks(patch))


def test_debug_handler_multi_arg_console_error_is_flagged():
    # Check 4's regex requires `err`/`X.stack` to be the SOLE argument
    # (`\(\s*...\s*\)`), so the extremely common
    # `console.error("context:", err.stack)` form — an extra leading
    # argument — never matches, silently passing a genuine ungated leak.
    patch = _diff('+console.error("Request failed:", err.stack);')
    assert any("Debug handlers" in f for f in run_static_checks(patch))


def test_debug_handler_gated_by_node_env_in_same_file_is_not_flagged():
    # Regression guard: the NODE_ENV gate legitimately lives on a context line
    # surrounding the added console.error/log call (main #56) — this must
    # keep passing once the check is scoped per-file.
    patch = _diff(
        " if (process.env.NODE_ENV !== 'production') {",
        "+  console.error(err.stack);",
        " }",
    )
    assert run_static_checks(patch) == []
