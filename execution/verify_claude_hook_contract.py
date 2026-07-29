"""Hook-contract gate for the live Claude runtime (~/.claude), backlog item 3.

Enforces the anti-drift policy's `generated-settings-from-hook-registry` rule:
settings.json must be rendered from the canonical hook registry rather than
edited as handwritten runtime truth, and the live hook entrypoints it wires
must stay portable across hosts.

Checks (all bounded and read-only against `runtime_root()`, which honors
CLAUDE_RUNTIME_ROOT for tests/sandboxes):

  1. the generators exist under the runtime root — scripts/hook_registry.py and
     scripts/sync_settings_config.py. A missing generator is a WARN (a partial
     runtime on this machine is not a violation), never a crash.
  2. settings.json is generator-aligned: its hooks section parses as a
     well-formed JSON object of event -> matcher list -> command entries, and
     every hook command that references a hook script under the runtime root
     resolves to a file that exists. A command referencing a hook file absent
     on disk = FAIL.
  3. live hook commands stay portable: a command that embeds a literal absolute
     Claude-home path (C:\\Users\\someone\\.claude in slash+escaping forms, or the
     literal /Users/someone/.claude) = FAIL. Portable ~ / $HOME /
     Path.home() / relative forms are fine.

Consumes config/claude_anti_drift_policy.json (the rule it enforces) and, for
context, config/claude_promotion_policy.json (the generated-config-integration
promotion gate).

Absolute-home detection is pattern-based (claude_runtime_target.pinned_home_paths)
rather than a literal list: a literal carrying this machine's own home was
rewritten by the public exporter into the portable form it exists to accept.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import (
    ANTI_DRIFT_POLICY_PATH,
    PROMOTION_POLICY_PATH,
    pinned_home_paths,
    runtime_root,
)

HOOK_CONTRACT_RULE_ID = "generated-settings-from-hook-registry"
EXPECTED_VERIFIER = "execution/verify_claude_hook_contract.py"

# Promotion-policy checklist gate this contract supports (consumed for context).
GENERATED_CONFIG_GATE_ID = "generated-config-integration"

# Generators, relative to the runtime root, that render settings.json.
GENERATOR_ARTIFACTS = ("scripts/hook_registry.py", "scripts/sync_settings_config.py")

SETTINGS_FILENAME = "settings.json"

#: Pattern-matched over ANY user's home rather than listed as literals, one of
#: which was this machine's own: the exporter rewrites "/Users/<owner>/.claude"
#: to "~/.claude", which put the portable form on the denylist and inverted this
#: check in the public tree. See claude_runtime_target.pinned_home_paths.
FORBIDDEN_RUNTIME_DIRS = (".claude",)

# Suffixes that mark a hook-script reference inside a command string.
SCRIPT_SUFFIXES = ("py", "js", "sh", "ts", "mjs", "cjs")

# Generic path-token pattern (NOT a path literal — safe to compile). Captures
# quoted/relative/absolute script references embedded in a command string.
_SCRIPT_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.$~/\\-]+\.(?:" + "|".join(SCRIPT_SUFFIXES) + r")"
)


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {
        "status": status,
        "label": label,
        "detail": detail,
    }


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {
        "OK": 0,
        "WARN": 0,
        "FAIL": 0,
    }
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0




def find_hook_contract_rule(payload: dict) -> dict | None:
    for rule in payload.get("rules", []):
        if rule.get("id") == HOOK_CONTRACT_RULE_ID:
            return rule
    return None


def collect_hook_commands(settings_payload: dict) -> tuple[list[str], list[str]]:
    """Validate the hooks section shape and return (commands, shape_errors).

    Expected shape: hooks is an object of event -> list of matcher objects, each
    optionally carrying a "hooks" list of {"type": "command", "command": str}
    entries. Structural deviations are returned as shape_errors (FAIL material);
    the commands actually found are still returned for downstream scanning.
    """
    commands: list[str] = []
    errors: list[str] = []
    hooks = settings_payload.get("hooks")
    if hooks is None:
        return commands, errors
    if not isinstance(hooks, dict):
        errors.append("hooks section is not a JSON object")
        return commands, errors

    for event, matchers in hooks.items():
        if not isinstance(matchers, list):
            errors.append(f"hooks.{event} is not a list")
            continue
        for i, matcher in enumerate(matchers):
            if not isinstance(matcher, dict):
                errors.append(f"hooks.{event}[{i}] is not an object")
                continue
            entry_hooks = matcher.get("hooks")
            if entry_hooks is None:
                continue
            if not isinstance(entry_hooks, list):
                errors.append(f"hooks.{event}[{i}].hooks is not a list")
                continue
            for j, entry in enumerate(entry_hooks):
                if not isinstance(entry, dict):
                    errors.append(f"hooks.{event}[{i}].hooks[{j}] is not an object")
                    continue
                if entry.get("type") != "command":
                    continue
                command = entry.get("command")
                if not isinstance(command, str) or not command.strip():
                    errors.append(
                        f"hooks.{event}[{i}].hooks[{j}] command is missing or empty"
                    )
                    continue
                commands.append(command)
    return commands, errors


def _to_runtime_relative(token: str) -> str | None:
    """Best-effort map a script token to a runtime-root-relative path.

    Returns None for tokens that cannot be reliably resolved under the runtime
    root (absolute paths outside a .claude home, home-anchored non-.claude
    paths, or paths that climb out of the root).
    """
    cleaned = token.strip().replace("\\", "/")
    marker = ".claude/"
    idx = cleaned.rfind(marker)
    if idx != -1:
        rel = cleaned[idx + len(marker):]
    elif cleaned.startswith(("~/", "$HOME/", "${HOME}/")):
        # Home-anchored but not via .claude — not reliably under the runtime root.
        return None
    elif cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        # Absolute path outside a .claude home — cannot map to the runtime root.
        return None
    else:
        rel = cleaned

    if rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    return rel


def extract_hook_script_refs(command: str) -> set[str]:
    """Runtime-root-relative hook-script references embedded in a command."""
    refs: set[str] = set()
    for token in _SCRIPT_TOKEN_RE.findall(command):
        normalized = token.replace("\\", "/")
        # Require a path separator so bare words that merely end in a script
        # suffix are not mistaken for file references.
        if "/" not in normalized:
            continue
        rel = _to_runtime_relative(token)
        if rel:
            refs.add(rel)
    return refs


def missing_hook_scripts(commands: list[str], root: Path) -> list[str]:
    """Referenced hook scripts that do not exist under the runtime root."""
    missing: set[str] = set()
    for command in commands:
        for rel in extract_hook_script_refs(command):
            if not (root / rel).is_file():
                missing.add(rel)
    return sorted(missing)


def hook_command_literal_offenders(
    commands: list[str], runtime_dirs: tuple[str, ...] = FORBIDDEN_RUNTIME_DIRS
) -> list[str]:
    """Hook commands embedding a literal absolute Claude-home path."""
    offenders: set[str] = set()
    for command in commands:
        hits = pinned_home_paths(command, *runtime_dirs)
        if hits:
            excerpt = command if len(command) <= 80 else command[:77] + "..."
            offenders.add(f"{excerpt} ({', '.join(hits)})")
    return sorted(offenders)


def _promotion_context_result(promotion_path: Path) -> dict[str, str]:
    payload, error = _load_json(promotion_path)
    if error:
        return _make_result(
            "WARN", "claude_promotion_policy.json", f"context unavailable ({error})"
        )
    checklist = (payload or {}).get("promotionChecklist") or []
    gate = next(
        (item for item in checklist if isinstance(item, dict) and item.get("id") == GENERATED_CONFIG_GATE_ID),
        None,
    )
    if gate is None:
        return _make_result(
            "WARN",
            "claude_promotion_policy.json",
            f"context gate {GENERATED_CONFIG_GATE_ID} not declared",
        )
    required = "required" if gate.get("required") is True else "optional"
    return _make_result(
        "OK",
        "claude_promotion_policy.json",
        f"context: {GENERATED_CONFIG_GATE_ID} promotion gate is {required}",
    )


def run_checks(runtime_root_dir: Path | None = None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "claude_anti_drift_policy.json", policy_error))
        return results

    rule = find_hook_contract_rule(policy_payload or {})
    if rule is None:
        results.append(
            _make_result(
                "FAIL",
                "claude_anti_drift_policy.json",
                f"missing {HOOK_CONTRACT_RULE_ID} rule",
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "claude_anti_drift_policy.json",
            f"hook contract rule '{HOOK_CONTRACT_RULE_ID}' declared",
        )
    )

    if rule.get("verifier") != EXPECTED_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                "claude-hook-contract.verifier-wiring",
                f"rule verifier is {rule.get('verifier')!r}, expected {EXPECTED_VERIFIER!r}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-hook-contract.verifier-wiring",
                f"rule routes through {EXPECTED_VERIFIER}",
            )
        )

    results.append(_promotion_context_result(PROMOTION_POLICY_PATH))

    live_root = runtime_root_dir if runtime_root_dir is not None else runtime_root()
    if not live_root.is_dir():
        results.append(
            _make_result(
                "WARN",
                "claude-hook-contract.runtime-root",
                f"runtime root not present on this machine: {live_root}",
            )
        )
        return results

    missing_generators = [
        artifact for artifact in GENERATOR_ARTIFACTS if not (live_root / artifact).is_file()
    ]
    if missing_generators:
        results.append(
            _make_result(
                "WARN",
                "claude-hook-contract.generators",
                "generator script(s) missing on this machine: " + ", ".join(missing_generators),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-hook-contract.generators",
                "settings generators present: " + ", ".join(GENERATOR_ARTIFACTS),
            )
        )

    settings_path = live_root / SETTINGS_FILENAME
    if not settings_path.is_file():
        results.append(
            _make_result(
                "WARN",
                "claude-hook-contract.settings-present",
                f"{SETTINGS_FILENAME} absent under runtime root: {settings_path}",
            )
        )
        return results

    settings_payload, settings_error = _load_json(settings_path)
    if settings_error:
        results.append(
            _make_result("FAIL", "claude-hook-contract.settings-json", settings_error)
        )
        return results

    payload = settings_payload or {}
    commands, shape_errors = collect_hook_commands(payload)

    if payload.get("hooks") is None:
        results.append(
            _make_result(
                "WARN",
                "claude-hook-contract.hooks-section",
                "settings.json declares no hooks section to validate",
            )
        )
    elif shape_errors:
        results.append(
            _make_result(
                "FAIL",
                "claude-hook-contract.hooks-section",
                "malformed hooks section: " + "; ".join(shape_errors),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-hook-contract.hooks-section",
                f"hooks section is a well-formed object with {len(commands)} command entries",
            )
        )

    missing_refs = missing_hook_scripts(commands, live_root)
    if missing_refs:
        results.append(
            _make_result(
                "FAIL",
                "claude-hook-contract.hook-scripts",
                "settings.json references hook file(s) absent on disk: " + ", ".join(missing_refs),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-hook-contract.hook-scripts",
                "every resolvable hook-script reference exists under the runtime root",
            )
        )

    literal_offenders = hook_command_literal_offenders(commands)
    if literal_offenders:
        results.append(
            _make_result(
                "FAIL",
                "claude-hook-contract.hook-portability",
                "hook command(s) embed a literal absolute Claude-home path: "
                + "; ".join(literal_offenders),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-hook-contract.hook-portability",
                "no hook command embeds a literal absolute Claude-home path",
            )
        )

    return results


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
