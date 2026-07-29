#!/usr/bin/env python3
import sys
import os
import argparse
import json
import re
from pathlib import Path

# Ensure Governance directory is on sys.path so we can import agentica_core
GOV_ROOT = os.environ.get("GOVERNANCE_ROOT") or str(Path(__file__).resolve().parents[2])
if GOV_ROOT not in sys.path:
    sys.path.insert(0, GOV_ROOT)

try:
    from agentica_core.llm.gateway import gateway
except ImportError:
    print(f"Error: Cannot import agentica_core from {GOV_ROOT}. Set GOVERNANCE_ROOT env var.")
    sys.exit(2)

SYSTEM_PROMPT = """You are a senior security checker auditing a proposed codebase patch for safety.
You must review the patch against the following Security Checklist:

1. CORS Wildcard: CORS must NOT be configured to allow wildcard '*' origins (e.g. `origin: '*'` or `cors()` with no origin restriction).
2. Rate Limiting: Rate limiters must be active and wired to endpoints (not just defined in config/env).
3. Security Headers: helmet() or equivalent security headers must be present in web app setups.
4. Input Length Caps: All user-facing input endpoints must enforce explicit length constraints.
5. CLI Argument Injection (CWE-88): Raw user input must NEVER be passed directly as a command-line argument to a subprocess (e.g. child_process.spawn). Use temp files or stdin instead.
6. Debug Handlers: Debug/exit handlers must not expose stack traces in production (must gate verbose error stacks by checking NODE_ENV !== 'production').
7. gitignore: Credentials and .env files must be gitignored (never added or modified in the patch).
8. Unused Config: Env vars defined must be read and used.
9. Absolute Paths: Absolute paths must not be hardcoded in SOURCE CODE files (.py, .ts, .js, .sh, ...), e.g. C:\\Users\\... or /home/... . Paths RECORDED in state/log/doc/data files (.jsonl, .json, .md, files under state/) are telemetry, not hardcoding — they are acceptable and must NOT fail this check.
10. Unsafe Subprocess: Subprocesses must not be spawned with `shell: true` to prevent command injection risks.

Return a JSON object containing EXACTLY these keys:
{
  "approved": <bool>,
  "failures": [<str>],
  "reason": "<str>"
}
"""

CODE_EXTENSIONS = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".sh", ".rb", ".go")


def _added_code_lines(patch_content: str):
    """Yield added lines from hunks whose target file is source code.

    State/docs/data files legitimately record absolute paths (exec logs,
    quarantine READMEs, policy JSON), so the Unix-abspath check applies only
    to code files; the Windows drive-letter check stays patch-wide.
    """
    target_is_code = False
    prev = ""
    for line in patch_content.splitlines():
        # A real file header is always preceded by its "--- " pair; an ADDED
        # content line that merely starts with "++ " renders as "+++ ..." and
        # must not flip the state (spoofable scoping in both directions).
        if line.startswith("+++ ") and prev.startswith("--- "):
            target_is_code = line[4:].strip().endswith(CODE_EXTENSIONS)
        elif target_is_code and line.startswith("+") and not line.startswith("+++"):
            yield line
        prev = line


def _added_code(patch_content: str) -> str:
    """The code a unified diff ADDS ('+' lines, minus the '+++' header).

    Removed and context lines must not trip the code checks — a remediation
    patch that deletes an insecure pattern would otherwise be rejected for
    containing it (main #56, 2026-07-18: "the security gate blocked the very
    fixes it exists to approve"). This was never applied on work — checks 1,
    2, 4, 6 below scanned raw patch_content wholesale until this merge; only
    check 5's absolute-path scoping (_added_code_lines, above) existed here.
    Non-diff input (no diff markers) is scanned wholesale.
    """
    lines = patch_content.splitlines()
    if not any(l.startswith(("+++", "---", "@@")) for l in lines):
        return patch_content
    return "\n".join(l[1:] for l in lines if l.startswith("+") and not l.startswith("+++"))


def run_static_checks(patch_content: str) -> list[str]:
    failures = []
    added = _added_code(patch_content)

    # 1. CORS check: Reject wildcard origin or unconfigured cors() middleware
    if re.search(r"\borigin\s*:\s*['\"]\*['\"]", added) or re.search(r"cors\(\s*\)", added):
        failures.append("CORS configured to allow wildcard '*' origin.")

    # 2. CLI Argument Injection (CWE-88) check
    if re.search(r"spawn\([^)]*(\+|\$\{)", added) or re.search(r"exec\([^)]*(\+|\$\{)", added):
        failures.append("Potential CLI argument injection (CWE-88): raw concatenation in spawn/exec call.")

    # 3. gitignore check (inspects diff headers, so it reads the raw patch).
    # Judged on the basename: committed templates (.env.example) and docs that
    # merely mention credentials in their name are not env/credential files
    # (2026 sweep PR #80 — a benign .env.example/credentials-doc edit was
    # silently killing valid autonomous remediations at this gate).
    for m in re.finditer(r"^\+\+\+\s+b/(\S+)", patch_content, re.MULTILINE):
        base = m.group(1).rsplit("/", 1)[-1].lower()
        is_env = base == ".env" or (base.startswith(".env.") and base not in
                                    (".env.example", ".env.sample", ".env.template", ".env.dist"))
        is_cred = "credentials" in base and not base.endswith((".md", ".rst", ".txt"))
        if is_env or is_cred:
            failures.append(".env or credentials file modified directly in the patch.")
            break

    # 4. Debug Handlers check (NODE_ENV gate may live on surrounding context lines,
    # so only the added line is scoped, not the NODE_ENV guard check — main #56)
    if re.search(r"console\.(error|log)\(\s*([a-zA-Z0-9_]+\.stack|err)\s*\)", added) and not "NODE_ENV" in patch_content:
        failures.append("Debug handlers exposing stack trace in production (missing NODE_ENV !== 'production' gate).")

    # 5. Absolute path check — Windows drive letters anywhere; Unix abspaths
    # only in added source-code lines (the patch-wide form auto-rejected every
    # governance patch whose state/doc files record absolute paths)
    if re.search(r"['\"][a-zA-Z]:\\[^\s'\"]*", patch_content) or any(
        re.search(r"['\"]/(home|Users|tmp)/[^\s'\"]*", line)
        for line in _added_code_lines(patch_content)
    ):
        failures.append("Hardcoded absolute paths detected in patch.")

    # 6. Unsafe Subprocess (shell: true) check
    if re.search(r"shell\s*:\s*true", added):
        failures.append("Subprocess spawned with 'shell: true' option (unsafe shell injection risk).")

    return failures


# --- Deterministic path-scope gate (pre-LLM; cannot be prompt-injected) ------
# git apply runs at the REPO ROOT, so a passing patch can name ANY repo path —
# including the files that gate the next patch. This layer rejects, with no LLM
# in the loop, any patch that touches the control plane, escapes via a traversal
# or absolute path, or is binary (opaque to review). It runs FIRST in main().
# Paths are read from the authoritative unified-diff headers (--- / +++ / rename
# / copy), never the space-ambiguous `diff --git` line, so "Order Samurai" (which
# contains a space) parses correctly.
PROTECTED_PATH_PATTERNS = [
    re.compile(r"^Governance/Order Samurai/execution/audit_remediation_patch\.py$"),  # the checker itself
    re.compile(r"^Governance/Order Samurai/bin/(bushido_check|remeasure_gate|render_surface_env)\.py$"),  # sibling gates
    re.compile(r"^Governance/api/src/"),          # the reflex engine + server + state
    re.compile(r"^Governance/Order Samurai/state/"),  # runtime + governance-control state (hitl_queue, skill_metadata, verdicts)
    re.compile(r"^Governance/Order Samurai/harness/"),  # declared editable-surface control plane
    re.compile(r"^Governance/schema/"),           # the wid_payload contract
    re.compile(r"(^|/)\.github/"),                 # CI / workflows
    re.compile(r"(^|/)hooks/"),                    # any hooks dir
    re.compile(r"^sub-bundles/"),                  # vendored claude/antigravity control plane (submodules)
    re.compile(r"(^|/)\.gitignore$"),
    re.compile(r"(^|/)settings\.json$"),
    re.compile(r"(^|/)\.env(\.|$)"),
    re.compile(r"(^|/)\.claude/"),
]


def _patch_target_paths(patch_content: str):
    """Yield (repo_relative_path, was_quoted) for every file the patch touches,
    plus a sentinel ('', 'BINARY') when a binary hunk is present. Reads only the
    authoritative headers so paths with spaces are handled correctly."""
    for raw in patch_content.splitlines():
        if raw.startswith("GIT binary patch") or raw.startswith("Binary files "):
            yield ("", "BINARY")
            continue
        path = None
        if raw.startswith("+++ ") or raw.startswith("--- "):
            path = raw[4:]
        elif raw.startswith("rename to ") or raw.startswith("copy to "):
            path = raw.split(" to ", 1)[1]
        elif raw.startswith("rename from ") or raw.startswith("copy from "):
            path = raw.split(" from ", 1)[1]
        else:
            continue
        path = path.split("\t", 1)[0].rstrip()   # drop a trailing tab-timestamp
        if path == "/dev/null" or not path:
            continue
        quoted = path.startswith('"')
        if quoted:
            path = path[1:]
            if path.endswith('"'):
                path = path[:-1]
        if path.startswith("a/") or path.startswith("b/"):
            path = path[2:]
        yield (path, "QUOTED" if quoted else "")


def check_path_scope(patch_content: str) -> list[str]:
    failures: list[str] = []
    for path, flag in _patch_target_paths(patch_content):
        if flag == "BINARY":
            failures.append("Binary patch hunk — opaque to review; rejected.")
            continue
        if flag == "QUOTED":
            failures.append(f"Quoted/special-character path (evasion vector): {path}")
        if ".." in path.split("/"):
            failures.append(f"Path traversal in patch target: {path}")
            continue
        if path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path):
            failures.append(f"Absolute path in patch target: {path}")
            continue
        for pat in PROTECTED_PATH_PATTERNS:
            if pat.search(path):
                failures.append(f"Patch touches a protected control-plane path: {path}")
                break
    # dedupe, preserve order
    seen, out = set(), []
    for f in failures:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit proposed remediation patches against the 8-point security checklist")
    parser.add_argument("--patch", required=True, type=Path, help="Path to the patch file")
    args = parser.parse_args()

    if not args.patch.exists():
        print(f"Error: Patch file {args.patch} does not exist.", file=sys.stderr)
        return 2

    try:
        patch_content = args.patch.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        print(f"Error reading patch file {args.patch}: {exc}", file=sys.stderr)
        return 2

    if not patch_content.strip():
        print("Patch is empty. Approving by default.")
        print(json.dumps({"approved": True, "failures": [], "reason": "Empty patch."}))
        return 0

    # Deterministic path-scope gate runs FIRST — it cannot be prompt-injected and
    # rejects control-plane / traversal / absolute / binary patches before the LLM.
    scope_failures = check_path_scope(patch_content)
    if scope_failures:
        result = {
            "approved": False,
            "failures": scope_failures,
            "reason": "Path-scope gate: patch touches control-plane or out-of-scope paths."
        }
        print(json.dumps(result, indent=2))
        print("Security Audit FAILED (Path Scope).", file=sys.stderr)
        for f in scope_failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    static_failures = run_static_checks(patch_content)
    if static_failures:
        result = {
            "approved": False,
            "failures": static_failures,
            "reason": "Static check failures against the security checklist."
        }
        print(json.dumps(result, indent=2))
        print("Security Audit FAILED (Static Analysis).", file=sys.stderr)
        for f in static_failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    user_prompt = f"""Please audit the following Git patch:

```diff
{patch_content}
```

Evaluate if it violates any items in the Security Checklist. Output your audit result in the specified JSON format.
"""

    try:
        response = gateway.generate_text(
            prompt=user_prompt,
            system_instruction=SYSTEM_PROMPT,
            temperature=0.0,
            # Pin the code-review local tier: without max_tokens the gateway
            # floors num_predict to 512 and gemma truncates before emitting the
            # JSON verdict; without response_schema Ollama never gets
            # format:"json"; without num_ctx a 22KB patch silently truncates
            # from the front; thinking builds need think=False or the whole
            # num_predict budget burns in `thinking`. Known limitation: the
            # gateway appends its default local model to this chain, so a 12b
            # failure (OOM/busy) degrades the verdict to the 4b tier rather
            # than failing closed.
            model_chain=["gemma4:12b"],
            max_tokens=2048,
            num_ctx=16384,
            think=False,
            response_schema={
                "type": "object",
                "required": ["approved", "failures", "reason"],
            },
        )
    except Exception as exc:
        print(f"Error calling LLM Gateway: {exc}", file=sys.stderr)
        # Fail safe
        return 1

    try:
        cleaned = response.strip()
        cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)
        
        result = json.loads(cleaned)
        
        approved = result.get("approved", False)
        failures = result.get("failures", [])
        reason = result.get("reason", "No reason provided.")

        print(json.dumps(result, indent=2))

        if approved:
            print("Security Audit PASSED.")
            return 0
        else:
            print(f"Security Audit FAILED: {reason}", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Error parsing LLM response: {exc}", file=sys.stderr)
        print(f"Raw response: {response}", file=sys.stderr)
        # Fail safe
        return 1

if __name__ == "__main__":
    sys.exit(main())
