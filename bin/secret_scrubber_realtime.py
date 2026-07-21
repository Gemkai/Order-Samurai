#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
secret_scrubber_realtime — PostToolUse/Write|Edit|MultiEdit|Bash|Agent|Read hook (async)
                         — PreToolUse/Bash|WebFetch hook (sync, --pre, blocking)

Post-mode: redacts secrets from high-risk files the moment they are written.
Pre-mode:  blocks Bash/WebFetch commands that carry db_connection_string or
           internal_ip patterns with an outbound indicator.  SECRET_SCRUBBER_BLOCK
           env controls staging: shadow (default) = log only; 1 = enforce (exit 2).
"""
from __future__ import annotations

import sys
from pathlib import Path

CLAUDE_ROOT = Path.home() / ".claude"
if str(CLAUDE_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(CLAUDE_ROOT / "scripts"))

from cli_io import configure_utf8_stdio
import argparse
import json
import os
import re
import shutil
from datetime import datetime

configure_utf8_stdio()

_REPO_ROOT     = Path(os.environ.get("SAMURAI_ROOT") or os.environ.get("ORDER_SAMURAI_ROOT") or Path(__file__).resolve().parent.parent)
CLAUDE_ROOT    = Path.home() / ".claude"
DATA           = CLAUDE_ROOT / "data"
QUARANTINE     = DATA / "quarantine" / "secrets"
LOG            = DATA / "secret_scrubber_realtime.jsonl"
ALLOWLIST_PATH = CLAUDE_ROOT / "config" / "exfil_allowlist.json"

# Exfiltration patterns
EXFILTRATION_PATTERNS = [
    {
        "name": "internal_ip",
        "pattern": re.compile(r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b")
    },
    {
        "name": "db_connection_string",
        "pattern": re.compile(r"\b(?:postgres|mysql|mongodb)://[a-zA-Z0-9_\-\.\+]+:[^@\s]+@[a-zA-Z0-9_\-\.:/]+\b")
    },
    {
        "name": "internal_path",
        "pattern": re.compile(r"(?:\\\\[A-Za-z0-9_\-\.]+\\[A-Za-z0-9_\-\.\$\\]+)|(?i:C:\\Users\\[A-Za-z0-9_\-\.]+\\AppData)|(?i:C:\\Users\\[A-Za-z0-9_\-\.]+\\(?:\.ssh|\.aws|\.kube|\.gitconfig|\.env)\b)")
    }
]

def _build_patterns():
    standard = []
    try:
        sys.path.insert(0, str(CLAUDE_ROOT / "scripts"))
        import secret_scrubber  # type: ignore
        for name, rx in secret_scrubber.PATTERNS:
            standard.append({"name": name, "pattern": rx})
    except Exception:
        pass
    return EXFILTRATION_PATTERNS + standard

PATTERNS = _build_patterns()

_OUTBOUND_RE = re.compile(r"\bcurl\b|\bwget\b|\bInvoke-WebRequest\b|\bhttps?://")

def _load_allowlist() -> list[str]:
    try:
        data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        return [str(e) for e in data.get("allowed", [])]
    except Exception:
        return []

_EXFIL_ALLOWLIST: list[str] = _load_allowlist()

def _is_high_risk(path: Path) -> bool:
    try:
        rel = path.relative_to(CLAUDE_ROOT)
    except Exception:
        return False
    parts = rel.parts
    name  = path.name
    suf   = path.suffix.lower()

    if "memory" in parts and suf == ".md":
        return True
    if name in ("GLOBAL_LESSONS.md", "CLAUDE.md"):
        return True
    if parts and parts[0] == "data" and suf in (".log", ".jsonl", ".md"):
        return True
    if ".tmp" in parts and "intelligence" in parts and suf == ".md":
        return True
    return False

def _read_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}

def _scan(text: str, patterns: list[tuple[str, re.Pattern]]) -> list[dict]:
    out: list[dict] = []
    for label, pattern in patterns:
        for m in pattern.finditer(text):
            out.append({
                "label":      label,
                "match_at":   m.start(),
                "secret_len": m.end() - m.start(),
            })
    return out

def _redact_in_place(path: Path, patterns: list[tuple[str, re.Pattern]]) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    redacted = text
    bytes_redacted = 0
    for label, pattern in patterns:
        def _repl(m, _label=label):
            nonlocal bytes_redacted
            bytes_redacted += m.end() - m.start()
            return f"[REDACTED:{_label}:{m.end()-m.start()}b]"
        redacted = pattern.sub(_repl, redacted)

    if redacted == text:
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d")
    qdir  = QUARANTINE / stamp
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        rel = path.relative_to(CLAUDE_ROOT)
        qpath = qdir / str(rel).replace("\\", "_").replace("/", "_")
        qpath = qpath.with_name(qpath.name + f".{int(datetime.now().timestamp())}")
        shutil.copy2(path, qpath)
    except Exception:
        return 0

    try:
        path.write_text(redacted, encoding="utf-8")
    except Exception:
        return 0

    return bytes_redacted

def atomic_jsonl_append(file_path: Path, entry: dict):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.strip():
                        lines.append(line)
        except Exception:
            pass
    lines.append(json.dumps(entry) + "\n")
    tmp_path = file_path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass
        tmp_path.replace(file_path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

def _log_pre_event(tool_name: str, findings: list[str], action: str) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": int(datetime.now().timestamp()),
        "mode":      "pre",
        "tool":      tool_name,
        "labels":    findings,
        "action":    action,
    }
    try:
        from jsonl_append import append_jsonl  # type: ignore
        append_jsonl(LOG, entry)
    except Exception:
        try:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def _emit_chain14_pre(tool_name: str, labels: str, action: str) -> None:
    repo_root = _REPO_ROOT
    event_log = repo_root / "state" / "kill_chain_events.jsonl"
    timestamp = datetime.utcnow().isoformat()
    if not timestamp.endswith("Z"):
        timestamp += "Z"
    event_entry = {
        "ts":                timestamp,
        "chain_id":          14,
        "event_type":        "model_exfiltration",
        "detail":            f"Matched exfil patterns: {labels}",
        "source":            f"secret_scrubber_realtime: {tool_name} (pre-block)",
        "remediation_action": action,
        "confidence":        1.0,
    }
    try:
        from jsonl_append import append_jsonl  # type: ignore
        append_jsonl(event_log, event_entry)
    except Exception:
        atomic_jsonl_append(event_log, event_entry)


def _run_pre() -> None:
    """PreToolUse BLOCK mode — scan Bash/WebFetch inputs for potential exfil."""
    try:
        payload = _read_stdin()
        if not payload:
            return

        tool_name = payload.get("tool_name") or payload.get("toolName") or ""
        tool_input = payload.get("tool_input") or {}

        block_mode = os.environ.get("SECRET_SCRUBBER_BLOCK", "shadow").lower()
        if block_mode == "0":
            return

        inherently_outbound = False
        if tool_name in ("Bash", "bash"):
            text_to_scan = str(tool_input.get("command", ""))
        elif tool_name in ("WebFetch", "web_fetch"):
            url    = str(tool_input.get("url", ""))
            prompt = str(tool_input.get("prompt", ""))
            text_to_scan = f"{url} {prompt}"
            inherently_outbound = True
        else:
            return

        if not text_to_scan.strip():
            return

        # Allowlist: if a destination token matches, pass through
        for allowed in _EXFIL_ALLOWLIST:
            if allowed in text_to_scan:
                return

        # Only block on db_connection_string or internal_ip (internal_path = log-only)
        block_findings = [
            p["name"]
            for p in EXFILTRATION_PATTERNS
            if p["name"] in ("db_connection_string", "internal_ip")
            and p["pattern"].search(text_to_scan)
        ]
        if not block_findings:
            return

        # Outbound indicator required for Bash; WebFetch is inherently outbound
        if not inherently_outbound and not _OUTBOUND_RE.search(text_to_scan):
            return

        labels = ", ".join(block_findings)

        if block_mode == "shadow":
            _log_pre_event(tool_name, block_findings, "would_block")
            sys.stderr.write(
                f"[secret_scrubber_realtime] shadow: would_block exfil via {tool_name} ({labels}).\n"
            )
            return

        # Enforce: block_mode == "1"
        _log_pre_event(tool_name, block_findings, "blocked")
        _emit_chain14_pre(tool_name, labels, "blocked")
        reason = f"Potential data exfiltration blocked: detected {labels} with outbound indicator."
        sys.stdout.write(json.dumps({"permissionDecision": "deny", "reason": reason}) + "\n")
        sys.stdout.flush()
        sys.exit(2)

    except SystemExit:
        raise
    except Exception:
        pass  # fail-open on any exception


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pre", action="store_true")
    args, _ = parser.parse_known_args()
    if args.pre:
        _run_pre()
        return

    payload = _read_stdin()
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    
    is_write = tool_name in ("Write", "Edit", "MultiEdit")
    is_output = tool_name in ("Bash", "Agent", "Read", "bash", "agent", "read")
    
    if not is_write and not is_output:
        return

    text_to_scan = ""
    source_name = ""
    cwd = payload.get("cwd") or payload.get("directory") or ""
    
    if is_write:
        fp = (payload.get("tool_input") or {}).get("file_path", "")
        if not fp:
            return
        path = Path(fp)
        if not path.exists():
            return
        if not _is_high_risk(path):
            return
        try:
            text_to_scan = path.read_text(encoding="utf-8", errors="replace")
            source_name = path.name
        except Exception:
            return
    else:
        res = payload.get("tool_response") or payload.get("toolOutput") or ""
        if isinstance(res, dict):
            text_to_scan = json.dumps(res)
        else:
            text_to_scan = str(res)
        source_name = f"{tool_name} output"

    if not text_to_scan.strip():
        return

    patterns_to_check = [(p["name"], p["pattern"]) for p in PATTERNS]
    findings = _scan(text_to_scan, patterns_to_check)
    if not findings:
        return

    bytes_redacted = 0
    if is_write:
        bytes_redacted = _redact_in_place(path, patterns_to_check)
        if bytes_redacted == 0:
            return
    else:
        bytes_redacted = sum(f["secret_len"] for f in findings)

    DATA.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":      int(datetime.now().timestamp()),
        "file":           str(path.relative_to(CLAUDE_ROOT)) if is_write else source_name,
        "findings_count": len(findings),
        "bytes_redacted": bytes_redacted,
        "labels":         [f["label"] for f in findings],
    }
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass

    exfil_labels = {"internal_ip", "db_connection_string", "internal_path"}
    exfil_findings = [f for f in findings if f["label"] in exfil_labels]
    
    if exfil_findings:
        repo_root = Path(cwd) if cwd else _REPO_ROOT
        event_log = repo_root / "state" / "kill_chain_events.jsonl"
        timestamp = datetime.utcnow().isoformat().replace("+00:00", "Z")
        if not timestamp.endswith("Z"):
            timestamp += "Z"
            
        event_entry = {
            "ts": timestamp,
            "chain_id": 14,
            "event_type": "model_exfiltration",
            "detail": f"Matched exfil patterns: {', '.join(set(f['label'] for f in exfil_findings))}",
            "source": f"secret_scrubber_realtime: {source_name}",
            "remediation_action": "redacted" if is_write else "logged",
            "confidence": 1.0
        }
        
        atomic_jsonl_append(event_log, event_entry)

    try:
        sys.path.insert(0, str(CLAUDE_ROOT / "scripts"))
        from notify_critical import send_notification  # type: ignore
        labels = ", ".join(sorted(set(f["label"] for f in findings)))
        send_notification(
            title="Claude Code: secret/exfiltration detected (auto-redacted/logged)",
            body=f"{source_name}: {len(findings)} match(es), types: {labels}.",
            level="critical",
        )
    except Exception:
        pass

    sys.stderr.write(
        f"[secret_scrubber_realtime] detected {len(findings)} matches in {source_name} "
        f"({', '.join(set(f['label'] for f in findings))}).\n"
    )

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
