"""Backlog item 4: verify the Claude runtime MCP contract.

Validates that the LIVE Claude runtime's `mcp.json` is generated from the
launcher-backed MCP registry and that optional servers use explicit activation
policy. Consumes the two policies that route through this verifier:

  - config/claude_anti_drift_policy.json   (rule: generated-mcp-from-launcher-registry)
  - config/claude_anti_sprawl_policy.json  (rule: optional-capabilities-must-declare-activation)

Checks, all bounded and read-only against `claude_runtime_target.runtime_root()`
(which honors CLAUDE_RUNTIME_ROOT for tests/sandboxes):

- the launcher + registry generators exist under the runtime root
  (scripts/launch_mcp_server.py, scripts/mcp_server_registry.py,
  scripts/sync_mcp_config.py) — missing = WARN, never crash
- `mcp.json` parses and is well-formed; a server entry embedding a literal
  absolute Claude-home path (C:\\Users\\someone\\.claude forms, the
  /Users/someone/.claude literal) = FAIL — that is exactly the drift a
  generated, launcher-backed config is supposed to prevent
- disabled-by-policy is distinguished from enabled-but-broken: a server marked
  disabled whose required activation env is unset is correctly gated (OK), NOT a
  failure; only an ENABLED server that is not launcher-backed (missing
  launcher / broken command) is a WARN. If `mcp.json` carries no
  machine-readable enabled/disabled or env-activation metadata, an honor-system
  OK row says so rather than fabricating a check.

Absolute-home detection is pattern-based (claude_runtime_target.pinned_home_paths)
rather than a literal list: a literal carrying this machine's own home was
rewritten by the public exporter into the portable form it exists to accept.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import (
    ANTI_DRIFT_POLICY_PATH,
    ANTI_SPRAWL_POLICY_PATH,
    pinned_home_paths,
    runtime_root,
)

EXPECTED_VERIFIER = "execution/verify_claude_mcp_contract.py"
ANTI_DRIFT_RULE_ID = "generated-mcp-from-launcher-registry"
ANTI_SPRAWL_RULE_ID = "optional-capabilities-must-declare-activation"

# Generators that must render mcp.json from the launcher-backed registry.
REQUIRED_LAUNCHER_SCRIPTS = (
    "scripts/launch_mcp_server.py",
    "scripts/mcp_server_registry.py",
    "scripts/sync_mcp_config.py",
)
MCP_CONFIG_RELPATH = "mcp.json"

# A launcher-backed server routes its command/args through the launcher script
# or the registry rather than an ad-hoc absolute path.
LAUNCHER_TOKENS = ("launch_mcp_server", "mcp_server_registry")

#: Pattern-matched over ANY user's home rather than listed as literals, one of
#: which was this machine's own: the exporter rewrites "/Users/<owner>/.claude"
#: to "~/.claude", which put the portable form on the denylist and inverted this
#: check in the public tree. See claude_runtime_target.pinned_home_paths.
FORBIDDEN_RUNTIME_DIRS = (".claude",)


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def find_rule(payload: dict, rule_id: str) -> dict | None:
    for rule in payload.get("rules", []):
        if rule.get("id") == rule_id:
            return rule
    return None


def server_entries(mcp_payload: dict) -> dict:
    """The mcpServers object, or {} when absent/malformed."""
    servers = mcp_payload.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def server_is_enabled(name: str, cfg: dict, mcp_payload: dict) -> bool:
    """Enabled unless an explicit disabled flag or an enabled-set says otherwise."""
    enabled_set = mcp_payload.get("enabledServers")
    if not isinstance(enabled_set, list):
        enabled_set = mcp_payload.get("enabled")
    if isinstance(enabled_set, list):
        return name in enabled_set
    return cfg.get("disabled") is not True


def required_activation_env(cfg: dict) -> list[str]:
    """Env keys whose value is a ${VAR}/$VAR placeholder — the declared
    activation condition the server must have provided to run."""
    env = cfg.get("env")
    if not isinstance(env, dict):
        return []
    required: list[str] = []
    for key, value in env.items():
        text = str(value).strip()
        if text.startswith("${") or (text.startswith("$") and len(text) > 1):
            required.append(key)
    return sorted(required)


def unset_env(keys: list[str]) -> list[str]:
    return [key for key in keys if not os.environ.get(key)]


def is_launcher_backed(cfg: dict) -> bool:
    blob = json.dumps(cfg)
    return any(token in blob for token in LAUNCHER_TOKENS)


def literal_home_hits(cfg: dict) -> list[str]:
    blob = json.dumps(cfg)
    return pinned_home_paths(blob, *FORBIDDEN_RUNTIME_DIRS)


def has_activation_metadata(mcp_payload: dict, servers: dict) -> bool:
    """Whether mcp.json carries any machine-readable enabled/disabled or
    env-activation metadata at all."""
    enabled_set = mcp_payload.get("enabledServers") or mcp_payload.get("enabled")
    if isinstance(enabled_set, list):
        return True
    for cfg in servers.values():
        if not isinstance(cfg, dict):
            continue
        if "disabled" in cfg or required_activation_env(cfg):
            return True
    return False


def _check_policy_rule(
    results: list[dict[str, str]],
    *,
    policy_path: Path,
    label: str,
    rule_id: str,
) -> bool:
    """Load a policy and confirm its MCP rule routes through this verifier.

    Returns False (and appends a FAIL) on any hard problem so the caller can
    short-circuit, mirroring the sibling verifiers' fail-fast on missing config.
    """
    payload, error = _load_json(policy_path)
    if error:
        results.append(_make_result("FAIL", label, error))
        return False
    rule = find_rule(payload or {}, rule_id)
    if rule is None:
        results.append(_make_result("FAIL", label, f"missing {rule_id} rule"))
        return False
    if rule.get("verifier") != EXPECTED_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                f"{label}.verifier-wiring",
                f"rule verifier is {rule.get('verifier')!r}, expected {EXPECTED_VERIFIER!r}",
            )
        )
        return False
    results.append(
        _make_result(
            "OK",
            label,
            f"rule '{rule_id}' declared and routes through {EXPECTED_VERIFIER}",
        )
    )
    return True


def run_checks(runtime_root_dir: Path | None = None) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    # Consume both policies that route through this verifier.
    if not _check_policy_rule(
        results,
        policy_path=ANTI_DRIFT_POLICY_PATH,
        label="claude_anti_drift_policy.json",
        rule_id=ANTI_DRIFT_RULE_ID,
    ):
        return results
    if not _check_policy_rule(
        results,
        policy_path=ANTI_SPRAWL_POLICY_PATH,
        label="claude_anti_sprawl_policy.json",
        rule_id=ANTI_SPRAWL_RULE_ID,
    ):
        return results

    live_root = runtime_root_dir if runtime_root_dir is not None else runtime_root()
    if not live_root.is_dir():
        results.append(
            _make_result(
                "WARN",
                "claude-mcp-contract.runtime-root",
                f"runtime root not present on this machine: {live_root}",
            )
        )
        return results

    _check_launcher_scripts(results, live_root)
    loaded = _check_mcp_json(results, live_root)
    if loaded is None:
        return results
    payload, servers = loaded
    _check_literal_home_paths(results, servers)
    _check_enabled_servers_are_launcher_backed(results, servers, payload)
    _check_activation_gating(results, servers, payload)
    return results


def _check_launcher_scripts(results: list[dict[str, str]], live_root: Path) -> None:
    """Check 1: the launcher + registry generators exist under the runtime root."""
    # Check 1: launcher + registry generators exist under the runtime root.
    missing_scripts = [
        rel for rel in REQUIRED_LAUNCHER_SCRIPTS if not (live_root / rel).is_file()
    ]
    if missing_scripts:
        results.append(
            _make_result(
                "WARN",
                "claude-mcp-contract.launcher-scripts",
                "launcher/registry generator(s) absent on this machine: "
                + ", ".join(missing_scripts),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-mcp-contract.launcher-scripts",
                "launcher and registry generators present: "
                + ", ".join(REQUIRED_LAUNCHER_SCRIPTS),
            )
        )



def _check_mcp_json(
    results: list[dict[str, str]], live_root: Path
) -> tuple[dict, dict] | None:
    """Check 2: mcp.json parses and declares servers.

    Returns (payload, servers), or None when the caller must stop — an absent,
    unparseable or server-less config leaves checks 3-5 with nothing to judge,
    and running them anyway would report conclusions about a file that was
    never read."""
    # Check 2: mcp.json parses and is well-formed.
    mcp_path = live_root / MCP_CONFIG_RELPATH
    mcp_payload, mcp_error = _load_json(mcp_path)
    if mcp_error == "missing":
        results.append(
            _make_result(
                "WARN",
                "claude-mcp-contract.mcp-json",
                f"mcp.json not present under runtime root: {mcp_path}",
            )
        )
        return None
    if mcp_error:
        results.append(
            _make_result("FAIL", "claude-mcp-contract.mcp-json", mcp_error)
        )
        return None

    payload = mcp_payload or {}
    servers = server_entries(payload)
    if not servers:
        results.append(
            _make_result(
                "WARN",
                "claude-mcp-contract.mcp-json",
                "mcp.json is well-formed but declares no mcpServers object to check",
            )
        )
        return None
    results.append(
        _make_result(
            "OK",
            "claude-mcp-contract.mcp-json",
            f"mcp.json is well-formed with {len(servers)} server entr"
            f"{'y' if len(servers) == 1 else 'ies'}",
        )
    )
    return payload, servers



def _check_literal_home_paths(results: list[dict[str, str]], servers: dict) -> None:
    """Check 3: no server entry embeds a literal absolute Claude-home path."""
    # Check 3: no server entry embeds a literal absolute Claude-home path.
    literal_offenders: list[str] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        hits = literal_home_hits(cfg)
        if hits:
            literal_offenders.append(f"{name} ({', '.join(hits)})")
    if literal_offenders:
        results.append(
            _make_result(
                "FAIL",
                "claude-mcp-contract.launcher-backed",
                "server entries embed literal absolute Claude-home paths instead of "
                "the launcher: " + ", ".join(sorted(literal_offenders)),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-mcp-contract.launcher-backed",
                "no server entry embeds a literal absolute Claude-home path",
            )
        )



def _check_enabled_servers_are_launcher_backed(
    results: list[dict[str, str]], servers: dict, payload: dict
) -> None:
    """Check 4: enabled servers are launcher-backed. Disabled ones are gated
    and deliberately not held to this."""
    # Check 4: enabled servers must be launcher-backed (missing launcher /
    # broken command). Disabled servers are gated and are not held to this.
    broken_enabled: list[str] = []
    enabled_count = 0
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if not server_is_enabled(name, cfg, payload):
            continue
        enabled_count += 1
        if not is_launcher_backed(cfg):
            broken_enabled.append(name)
    if broken_enabled:
        results.append(
            _make_result(
                "WARN",
                "claude-mcp-contract.enabled-servers",
                "enabled server(s) are not launcher-backed (enabled-but-broken): "
                + ", ".join(sorted(broken_enabled)),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-mcp-contract.enabled-servers",
                f"all {enabled_count} enabled server(s) are launcher-backed",
            )
        )



def _check_activation_gating(
    results: list[dict[str, str]], servers: dict, payload: dict
) -> None:
    """Check 5: disabled-by-policy is distinguished from enabled-but-broken."""
    # Check 5: distinguish disabled-by-policy from enabled-but-broken. A server
    # marked disabled whose required activation env is unset is correctly gated.
    if not has_activation_metadata(payload, servers):
        results.append(
            _make_result(
                "OK",
                "claude-mcp-contract.activation-gating",
                "mcp.json carries no machine-readable enabled/disabled or "
                "env-activation metadata; not fabricating a gating check "
                "(honor-system)",
            )
        )
        return

    disabled_names: list[str] = []
    correctly_gated: list[str] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if server_is_enabled(name, cfg, payload):
            continue
        disabled_names.append(name)
        if unset_env(required_activation_env(cfg)):
            correctly_gated.append(name)
    gated_note = (
        f"; {len(correctly_gated)} correctly gated with unset activation env: "
        + ", ".join(sorted(correctly_gated))
        if correctly_gated
        else ""
    )
    results.append(
        _make_result(
            "OK",
            "claude-mcp-contract.activation-gating",
            f"{len(disabled_names)} server(s) disabled-by-policy (not treated as "
            f"broken){gated_note}",
        )
    )



def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
