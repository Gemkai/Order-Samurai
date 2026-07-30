#!/usr/bin/env python3
"""MCP connectivity smoke test — RESOLVE, never spawn (Wargame 01, Move 3).

Feeds the graded metric `MCP_Smoke_Fails` (bow/Activity). The Python aggregator
reads the JSON this writes:
    agentica_core/scouts/__init__.py -> reads ~/.claude/data/mcp_smoke_test.json
    -> out["mcp_smoke_fails"] = int(sm["fail_count"])
    -> aggregate.py sets pillars.bow.Activity.MCP_Smoke_Fails

Why resolve-not-spawn: MCP servers are stdio processes; launching one blocks or
triggers npx downloads. We only ask "can this configured server's launch target
be found on disk / does its host resolve?" without executing anything:

  * disabled server            -> skipped (never counted as a failure)
  * url / remote server        -> PASS iff the URL parses and its host resolves
                                  via socket.getaddrinfo (5s timeout, no HTTP call)
  * `npx <pkg>` server         -> PASS iff the package dir exists under a
                                  node_modules / the npx cache (no `npx` run)
  * python-launcher server     -> PASS iff the interpreter resolves AND the
                                  launcher script it runpath's actually exists
  * other command server       -> PASS iff shutil.which(command) resolves

A server that cannot resolve its launch target cannot start — that is a real,
honest failure, reported per-server in `checked`. The probe is never softened to
manufacture a green (Wargame 01 ABORT-3).

Config sources (read-only): ~/.claude/mcp.json and any project-scoped
mcpServers in ~/.claude.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
DATA_DIR = HOME / ".claude" / "data"
OUT_JSON = DATA_DIR / "mcp_smoke_test.json"
CONFIG_PATHS = [HOME / ".claude" / ("mcp" + ".json"), HOME / ".claude.json"]
DNS_TIMEOUT_S = 5.0


def _load_servers() -> dict[str, dict]:
    """Merge mcpServers from every config source (top-level + project-scoped)."""
    servers: dict[str, dict] = {}
    for path in CONFIG_PATHS:
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for name, cfg in (doc.get("mcpServers") or {}).items():
            if isinstance(cfg, dict):
                servers.setdefault(name, cfg)
        for pcfg in (doc.get("projects") or {}).values():
            if not isinstance(pcfg, dict):
                continue
            for name, cfg in (pcfg.get("mcpServers") or {}).items():
                if isinstance(cfg, dict):
                    servers.setdefault(name, cfg)
    return servers


def _host_resolves(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        socket.setdefaulttimeout(DNS_TIMEOUT_S)
        socket.getaddrinfo(host, None)
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)


def _npx_package_present(pkg: str) -> bool:
    """Best-effort: is the npx package already installed (no `npx` execution)?"""
    if pkg.startswith("@"):
        # scoped package: only strip a version if there's an "@" after the
        # scope separator (e.g. "@scope/name@1.2.3") — a bare scoped package
        # with no version pin (e.g. "@scope/name") has just the one leading
        # "@", and rsplit-ing on it would collapse the name to "".
        pkg = pkg.rsplit("@", 1)[0] if "@" in pkg[1:] else pkg
    else:
        pkg = pkg.lstrip("-").split("@")[0]
    candidates = [
        HOME / ".npm" / "_npx",  # npx cache root — scan below
    ]
    # any node_modules/<pkg> on common roots
    roots = [Path.cwd(), HOME, HOME / ".claude"]
    for root in roots:
        if (root / "node_modules" / pkg).exists():
            return True
    # npx cache: <hash>/node_modules/<pkg>
    npx_cache = HOME / ".npm" / "_npx"
    if npx_cache.exists():
        try:
            for entry in npx_cache.iterdir():
                if (entry / "node_modules" / pkg).exists():
                    return True
        except Exception:
            pass
    _ = candidates
    return False


def _launcher_script_from_args(args: list) -> Path | None:
    """python-launcher pattern: `-c "...script=<home>/.claude/scripts/<file>.py..."`.
    Return the launcher script path it will runpath, if present."""
    for a in args:
        if not isinstance(a, str):
            continue
        m = re.search(r"scripts/([A-Za-z0-9_./-]+\.py)", a)
        if m:
            return HOME / ".claude" / "scripts" / m.group(1)
    return None


def _probe(name: str, cfg: dict) -> dict:
    if cfg.get("disabled") is True:
        return {"name": name, "status": "skipped", "reason": "disabled"}

    url = cfg.get("url")
    ctype = cfg.get("type")
    if url or ctype in ("url", "sse", "http"):
        target = url or cfg.get("endpoint") or ""
        ok = _host_resolves(target)
        return {"name": name, "status": "pass" if ok else "fail",
                "reason": "host resolves" if ok else f"host unresolved: {target}"}

    command = cfg.get("command")
    args = cfg.get("args") or []
    if not command:
        return {"name": name, "status": "fail", "reason": "no command and no url"}

    if shutil.which(command) is None:
        return {"name": name, "status": "fail", "reason": f"command not found: {command}"}

    # `npx <pkg>` — resolve the package, never run npx.
    if os.path.basename(command) in ("npx", "npx.cmd"):
        pkg = next((a for a in args if isinstance(a, str) and not a.startswith("-")), None)
        if pkg and not _npx_package_present(pkg):
            return {"name": name, "status": "fail", "reason": f"npx package not installed: {pkg}"}
        return {"name": name, "status": "pass", "reason": "npx package present"}

    # python-launcher pattern — the interpreter resolves, but the script it
    # runpath's must exist too, or the server cannot start.
    if os.path.basename(command).startswith("python"):
        launcher = _launcher_script_from_args(args)
        if launcher is not None and not launcher.exists():
            return {"name": name, "status": "fail",
                    "reason": f"launcher script missing: {launcher}"}

    return {"name": name, "status": "pass", "reason": f"command resolves: {command}"}


def main() -> int:
    servers = _load_servers()
    checked = [_probe(name, cfg) for name, cfg in sorted(servers.items())]
    fail_count = sum(1 for c in checked if c["status"] == "fail")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "fail_count": fail_count,
                "checked": checked,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"mcp_smoke_test: {fail_count} fail / {len(checked)} checked -> {OUT_JSON}")
    for c in checked:
        if c["status"] == "fail":
            print(f"  FAIL {c['name']}: {c['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
