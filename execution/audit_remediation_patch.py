#!/usr/bin/env python3
import sys
import os
import argparse
import json
import re
from pathlib import Path

# Ensure Governance directory is on sys.path so we can import agentica_core
GOV_ROOT = os.environ.get("GOVERNANCE_ROOT") or str(Path(__file__).resolve().parents[1])
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
9. Absolute Paths: Absolute paths must not be hardcoded (e.g. C:\\Users\\... or /home/...).
10. Unsafe Subprocess: Subprocesses must not be spawned with `shell: true` to prevent command injection risks.

Return a JSON object containing EXACTLY these keys:
{
  "approved": <bool>,
  "failures": [<str>],
  "reason": "<str>"
}
"""

def run_static_checks(patch_content: str) -> list[str]:
    failures = []
    
    # 1. CORS check: Reject wildcard origin or unconfigured cors() middleware
    if re.search(r"\borigin\s*:\s*['\"]\*['\"]", patch_content) or re.search(r"cors\(\s*\)", patch_content):
        failures.append("CORS configured to allow wildcard '*' origin.")
        
    # 2. CLI Argument Injection (CWE-88) check
    if re.search(r"spawn\([^)]*(\+|\$\{)", patch_content) or re.search(r"exec\([^)]*(\+|\$\{)", patch_content):
        failures.append("Potential CLI argument injection (CWE-88): raw concatenation in spawn/exec call.")
        
    # 3. gitignore check
    if re.search(r"\+\+\+\s+b/.*?\.env", patch_content) or re.search(r"\+\+\+\s+b/.*?credentials", patch_content):
        failures.append(".env or credentials file modified directly in the patch.")
        
    # 4. Debug Handlers check
    if re.search(r"console\.(error|log)\(\s*([a-zA-Z0-9_]+\.stack|err)\s*\)", patch_content) and not "NODE_ENV" in patch_content:
        failures.append("Debug handlers exposing stack trace in production (missing NODE_ENV !== 'production' gate).")

    # 5. Absolute path check
    if re.search(r"['\"][a-zA-Z]:\\[^\s'\"]*", patch_content) or re.search(r"['\"]/(home|Users|tmp)/[^\s'\"]*", patch_content):
        failures.append("Hardcoded absolute paths detected in patch.")

    # 6. Unsafe Subprocess (shell: true) check
    if re.search(r"shell\s*:\s*true", patch_content):
        failures.append("Subprocess spawned with 'shell: true' option (unsafe shell injection risk).")

    return failures

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

        # The deterministic static gate above (run_static_checks) is the authoritative allow/deny
        # decision. This LLM stage is advisory: it may only VETO (raise failures / disapprove),
        # never grant approval on its own. So require a STRICT-boolean approval AND zero raised
        # failures — a contradictory or prompt-injected verdict (e.g. approved:true with failures,
        # or a non-bool "approved") fails closed rather than rubber-stamping the patch.
        if approved is True and not failures:
            print("Security Audit PASSED (static gate + LLM advisory, no veto).")
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
