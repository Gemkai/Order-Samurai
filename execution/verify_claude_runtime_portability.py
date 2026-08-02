#!/usr/bin/env python3
"""Verify the Claude runtime is portable across hosts.

Scorecard category `runtime_portability`: the runtime must prove zero pinned
Claude-home commands, zero bash-only live hooks, and zero direct launcher
bypass. This is the portability cross-cut over the hook + MCP surfaces — a
focused lens, not a re-run of the full hook/mcp contract verifiers.

Read-only. Standard {status,label,detail} rows + summarize()/main() convention.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import (  # type: ignore[import-not-found]  # noqa: E402
    ANTI_DRIFT_POLICY_PATH,
    pinned_home_paths,
    runtime_root,
)

#: Runtime homes a portable command must never pin, POSIX-spelled. The matcher
#: (claude_runtime_target.pinned_home_paths) is a pattern over any user's home,
#: not a literal list containing this machine's — see the note beside it: the
#: exporter scrubbed the literal into "~/.claude" and inverted this check.
PINNED_RUNTIME_DIRS = (".claude",)


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def _pinned_home(text: str) -> str | None:
    hits = pinned_home_paths(text, *PINNED_RUNTIME_DIRS)
    return hits[0] if hits else None


def _hook_commands(settings: dict) -> list[str]:
    commands: list[str] = []
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        return commands
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for hook in (entry or {}).get("hooks", []) if isinstance(entry, dict) else []:
                cmd = hook.get("command") if isinstance(hook, dict) else None
                if isinstance(cmd, str):
                    commands.append(cmd)
    return commands


def run_checks(*, runtime_root_dir: Path | None = None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    runtime = runtime_root_dir or runtime_root()

    if not ANTI_DRIFT_POLICY_PATH.exists():
        return [_make_result("FAIL", "runtime_portability.policy",
                             f"anti-drift policy missing: {ANTI_DRIFT_POLICY_PATH}")]
    if not runtime.exists():
        return [_make_result("WARN", "runtime_portability.root",
                             f"runtime root missing on this machine: {runtime} — checks skipped")]

    settings_path = runtime / "settings.json"
    mcp_path = runtime / "mcp.json"

    # 1. Zero pinned Claude-home commands (hooks + mcp servers).
    pinned: list[str] = []
    hook_commands: list[str] = []
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            hook_commands = _hook_commands(settings)
            for cmd in hook_commands:
                lit = _pinned_home(cmd)
                if lit:
                    pinned.append(f"settings.json hook `{cmd[:60]}` pins {lit}")
        except (ValueError, OSError) as exc:
            results.append(_make_result("FAIL", "runtime_portability.settings-parse",
                                        f"settings.json unreadable: {exc}"))
    else:
        results.append(_make_result("WARN", "runtime_portability.settings",
                                    "settings.json absent — hook portability unchecked"))

    mcp_servers: dict = {}
    if mcp_path.exists():
        try:
            mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp_servers = mcp.get("mcpServers", mcp) if isinstance(mcp, dict) else {}
            for name, spec in mcp_servers.items() if isinstance(mcp_servers, dict) else []:
                lit = _pinned_home(json.dumps(spec))
                if lit:
                    pinned.append(f"mcp.json server `{name}` pins {lit}")
        except (ValueError, OSError) as exc:
            results.append(_make_result("FAIL", "runtime_portability.mcp-parse",
                                        f"mcp.json unreadable: {exc}"))
    else:
        results.append(_make_result("WARN", "runtime_portability.mcp",
                                    "mcp.json absent — launcher portability unchecked"))

    results.append(
        _make_result("FAIL", "runtime_portability.pinned-home-commands",
                     "; ".join(pinned)) if pinned else
        _make_result("OK", "runtime_portability.pinned-home-commands",
                     "no runtime command pins an absolute Claude-home path"))

    # 2. Zero bash-only live hooks: a hook whose command hard-depends on `bash`
    # (bash -c ..., or a .sh entrypoint) is non-portable to hosts without it.
    bash_only = [c for c in hook_commands
                 if c.strip().startswith(("bash ", "bash\t", "/bin/bash", "sh "))
                 or (c.split() and ".sh" in c.split()[0])]
    results.append(
        _make_result("WARN", "runtime_portability.bash-only-hooks",
                     f"{len(bash_only)} hook(s) depend on bash/.sh: "
                     + "; ".join(c[:50] for c in bash_only[:3])) if bash_only else
        _make_result("OK", "runtime_portability.bash-only-hooks",
                     "no live hook hard-depends on bash/.sh"))

    # 3. Zero direct launcher bypass: an enabled mcp server whose command is a
    # raw interpreter/binary rather than routed through the launcher script.
    bypass: list[str] = []
    if isinstance(mcp_servers, dict):
        for name, spec in mcp_servers.items():
            if not isinstance(spec, dict) or spec.get("disabled") is True:
                continue
            blob = json.dumps(spec)
            if "launch_mcp_server" not in blob and "mcp_server_registry" not in blob:
                bypass.append(name)
    results.append(
        _make_result("WARN", "runtime_portability.launcher-bypass",
                     f"{len(bypass)} enabled server(s) not routed through the launcher: "
                     + ", ".join(bypass[:5])) if bypass else
        _make_result("OK", "runtime_portability.launcher-bypass",
                     "every enabled MCP server routes through the launcher"))

    return results


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
