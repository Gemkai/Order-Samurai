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
except ModuleNotFoundError as exc:
    # Name the ACTUAL missing module and the interpreter that lacked it. The
    # 2026-07-20/26 'audit_rejected' failures (exit 2) were NOT a bad
    # GOVERNANCE_ROOT — the reflex engine's bare-'python3' spawn resolved to the
    # CommandLineTools system python, which had no 'requests' (transitive via
    # agentica_core.llm.gateway). The old blanket message sent the diagnosis in
    # the wrong direction while the only two real patches ever produced died here.
    # Keep the literal exception class name in the message: reflex-engine.ts's
    # AUDIT_ENV_ERROR_RE (/Cannot import agentica_core|ModuleNotFoundError|ImportError/)
    # classifies this failure from stdout+stderr, and a message that doesn't
    # contain one of those three tokens gets misclassified as 'audit_rejected'
    # (a genuine security veto) instead of 'audit_env_error' -- reintroducing
    # the exact misdiagnosis that killed the only two real patches this system
    # ever produced. Don't reword this without checking that regex.
    print(f"Error: ModuleNotFoundError: Module '{exc.name}' not found under "
          f"interpreter {sys.executable} (GOVERNANCE_ROOT={GOV_ROOT}). Install "
          f"the missing module for THIS interpreter, or fix the PATH the reflex "
          f"engine resolves 'python3' from.")
    sys.exit(2)
except ImportError as exc:
    # Non-module-shaped import failure (broken symbol, circular import): keep the
    # GOVERNANCE_ROOT hint, but name the real error and the interpreter too.
    print(f"Error: Cannot import agentica_core from {GOV_ROOT} under interpreter "
          f"{sys.executable}: {exc}. Set GOVERNANCE_ROOT env var.")
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

    # 3. gitignore check (inspects diff headers via the same space-safe
    # _patch_target_paths parser check_path_scope uses -- a `\S+` regex on the
    # raw header cannot match a path with a space, e.g. this repo's own
    # "Order Samurai" subtree, silently defeating the check).
    # Judged on the basename: committed templates (.env.example) and docs that
    # merely mention credentials in their name are not env/credential files
    # (2026 sweep PR #80 — a benign .env.example/credentials-doc edit was
    # silently killing valid autonomous remediations at this gate).
    for path, flag in _patch_target_paths(patch_content):
        if flag == "BINARY" or not path:
            continue
        base = path.rsplit("/", 1)[-1].lower()
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
    re.compile(r"^Governance/Order Samurai/bin/"),  # ALL of bin/ — see REMEDIABLE_BIN_ALLOWLIST
    re.compile(r"^Governance/Order Samurai/prompts/"),  # the meditation/ronin prompts that govern the NEXT patch
    re.compile(r"^Governance/api/src/"),          # the reflex engine + server + state
    re.compile(r"^Governance/Order Samurai/state/"),  # runtime + governance-control state (hitl_queue, skill_metadata, verdicts)
    re.compile(r"^Governance/Order Samurai/harness/"),  # declared editable-surface control plane
    # 2026-08-16 audit: these two were the gap this list's own rationale describes.
    # config/ holds the executable policy contracts (root_hygiene_policy.json et al)
    # and execution/ holds the verify_*.py verifiers doctor.py aggregates — both are
    # live grading inputs. Only audit_remediation_patch.py itself was protected inside
    # execution/, so a reflex firing on e.g. metric:brush:Root_Hygiene_Issues could
    # emit a patch deleting the failing rule from its own policy, or edit the verifier
    # to stop emitting the FAIL that gates it, and the metric would go green without
    # the underlying condition changing.
    re.compile(r"^Governance/Order Samurai/config/"),  # executable policy contracts
    re.compile(r"^Governance/Order Samurai/execution/"),  # the verifiers that grade the patch
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


# Carve-out for PROTECTED_PATH_PATTERNS: paths that match a protected pattern but are
# nonetheless legitimate autonomous-remediation targets. Exact repo-relative paths only —
# no globs, so a carve-out can never widen by accident.
#
# EMPTY BY MEASUREMENT, NOT BY OVERSIGHT (2026-08-09). When bin/ was widened from 3 named
# gates to the whole directory, the obvious worry was starving the autonomous loop of
# legitimate targets. Measured before assuming: across all 165 rows of
# state/exec_log.jsonl, applied remediations have touched bin/ EXACTLY ZERO times. And on
# inspection all 50 files there are governance machinery — gates and audits, the
# ronin/samurai CLIs, the remediation pipeline itself, or escalation channels. Two worth
# naming: bin/wiki_link.py carries the breach thresholds that decide whether a metric is
# failing (lowering them was the explicitly-rejected shortcut in the
# remediation-first-live-fire goal), and bin/hitl_alerts.py IS the human escalation path.
# An agent editing either could suppress its own alarm. Seeding this list with "probably
# safe" guesses would hand back exactly the self-modification surface the widening closes.
#
# TO ADD ONE: append the exact path and the evidence that a real remediation needed it —
# an exec_log row or a blocked patch. The rejection message below names this list, so a
# genuinely-blocked remediation is self-diagnosing rather than a mystery.
REMEDIABLE_BIN_ALLOWLIST: frozenset[str] = frozenset()


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
        if path in REMEDIABLE_BIN_ALLOWLIST:
            continue  # explicitly ratified remediation target — see that list's rationale
        # git apply (no -p flag) defaults to -p1: it strips exactly ONE
        # leading path component from a --- / +++ header, regardless of its
        # literal name. _patch_target_paths only strips the literal "a/"/
        # "b/" git-diff convention, so a patch generated with e.g.
        # --src-prefix=x/ --dst-prefix=y/ still names "y/Governance/api/
        # src/reflex-engine.ts" here while the real `git apply` call in
        # reflex-engine.ts strips "y/" and writes to "Governance/api/src/
        # reflex-engine.ts" — bypassing every ^-anchored protected pattern.
        # Evaluate both the as-given path and the one-component-stripped
        # variant so an unconventional prefix can't hide the real target.
        candidates = {path}
        if "/" in path:
            candidates.add(path.split("/", 1)[1])
        for pat in PROTECTED_PATH_PATTERNS:
            if any(pat.search(c) for c in candidates):
                failures.append(
                    f"Patch touches a protected control-plane path: {path} "
                    f"(if this is a legitimate remediation target, add it to "
                    f"REMEDIABLE_BIN_ALLOWLIST in {Path(__file__).name} with evidence)"
                )
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

        # response_schema only declares "approved" as a required key, not its
        # JSON type -- a stringified "false" (or "no", or a non-empty list)
        # is truthy in Python and must not be treated as approval. Only the
        # real boolean True passes.
        approved = result.get("approved") is True
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
