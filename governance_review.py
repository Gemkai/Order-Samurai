#!/usr/bin/env python3
"""Adversarial governance code review — three independent models watch the watchers.

Reads the core governance files and sends them for review to:
  - Gemini 2.0 Flash   (GEMINI_API_KEY env var)
  - GPT-4o             (OPENAI_API_KEY env var)
  - gemma4:12b         (Ollama at localhost:11434 — always available)

Models run in parallel. Results are synthesized into:
  - docs/GOVERNANCE_REVIEW.md  — human-readable report
  - docs/governance_findings.json — machine-readable; future hook for aggregate.py metric

Usage:
  python governance_review.py              # review all files
  python governance_review.py --dry-run   # print what would be reviewed, no API calls
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from env_path into os.environ.

    Uses python-dotenv when available; falls back to a minimal built-in parser
    so the script has no hard dependency on the library.
    """
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)  # override=False: real env vars win
    except ImportError:
        # Minimal fallback: handles KEY=value and KEY="value", skips # comments
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)  # setdefault: real env vars win

# ── Paths ────────────────────────────────────────────────────────────────────

_THIS   = Path(__file__).resolve()
_GOV    = _THIS.parent
_DOCS   = _GOV / "docs"
_REPORT = _DOCS / "GOVERNANCE_REVIEW.md"
_JSON   = _DOCS / "governance_findings.json"

# Governance files that form the "watcher" — these are what we review.
_REVIEW_FILES: list[tuple[str, Path]] = [
    ("reflex-engine.ts",  _GOV / "api" / "src" / "reflex-engine.ts"),
    ("dojo.ts",           _GOV / "api" / "src" / "dojo.ts"),
    ("server.ts",         _GOV / "api" / "src" / "server.ts"),
    ("aggregate.py",      _GOV / "agentica_core" / "aggregate.py"),
    ("insights.py",       _GOV / "agentica_core" / "insights.py"),
    ("reflexes.py",       _GOV / "agentica_core" / "reflexes.py"),
]

# ── Model config ──────────────────────────────────────────────────────────────

REVIEW_PROMPT_TMPL = """You are an adversarial code reviewer. Review the following governance system file.
This code is the "watcher" — it monitors an AI operating system for health issues and fires
autonomous remediation commands. Bugs here mean the OS goes blind or takes wrong actions.

Flag ONLY real correctness defects, logic errors, security risks, and reliability failures.
Skip style, naming, and minor inefficiencies unless they cause real failure modes.

Use exactly this severity scale:
  CRITICAL — will cause incorrect behavior or data loss under reachable conditions
  HIGH     — will cause failures under edge cases or concurrent load
  MEDIUM   — could cause silent misbehavior over time
  LOW      — minor robustness gap, not urgent

For each finding output one line:
  [SEVERITY] FunctionOrSection: what breaks and why

Then a brief explanation (2-4 sentences max).
End with: TOTAL: X critical, Y high, Z medium, W low

File: {filename}
```
{code}
```"""


def _call_gemini(code: str, filename: str, key: str, timeout: int = 120) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": REVIEW_PROMPT_TMPL.format(filename=filename, code=code)}]}],
        "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.1},
    }).encode("utf-8")
    # key goes in a header, not the query string — URLs leak into logs/proxies
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    return resp["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai(code: str, filename: str, key: str, model: str = "gpt-4o", timeout: int = 120) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": REVIEW_PROMPT_TMPL.format(filename=filename, code=code)}],
        "max_tokens": 1500,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    return resp["choices"][0]["message"]["content"]


def _call_ollama(code: str, filename: str, model: str = "gemma4:12b", timeout: int = 300) -> str:
    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": REVIEW_PROMPT_TMPL.format(filename=filename, code=code)}],
        "max_tokens": 1500,
        "temperature": 0.1,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    message = resp["choices"][0]["message"]
    # Thinking models (deepseek-r1) can return empty content with the review in
    # the reasoning field; an empty review is a failure, never a zero-finding
    # success (CLAUDE.md "Local LLM Routing" guards). Inline copy by design —
    # this script is standalone stdlib-only; canonical implementation lives in
    # agentica_core/llm/local_guards.py, keep in sync.
    content = (message.get("content") or "").strip() or (
        message.get("reasoning") or ""
    ).strip()
    if not content:
        raise ValueError(f"empty response from local model {model}")
    return content


# ── Parallel dispatch ─────────────────────────────────────────────────────────

def _review_file(filename: str, code: str, gemini_key: str | None, openai_key: str | None) -> dict:
    results: dict[str, str] = {}
    errors:  dict[str, str] = {}
    lock = threading.Lock()

    def run(name: str, fn):
        try:
            text = fn()
            with lock:
                results[name] = text
        except Exception as exc:
            with lock:
                errors[name] = str(exc)

    threads = []

    if gemini_key:
        t = threading.Thread(target=run, args=("gemini-2.0-flash", lambda: _call_gemini(code, filename, gemini_key)))
        threads.append(t)
    else:
        errors["gemini-2.0-flash"] = "GEMINI_API_KEY not set"

    if openai_key:
        t = threading.Thread(target=run, args=("gpt-4o", lambda: _call_openai(code, filename, openai_key)))
        threads.append(t)
    else:
        errors["gpt-4o"] = "OPENAI_API_KEY not set"

    # local review via Ollama — always attempt (gemma4:12b: readable critique,
    # no thinking-model empty-content trap)
    t = threading.Thread(target=run, args=("gemma4:12b", lambda: _call_ollama(code, filename)))
    threads.append(t)

    for th in threads:
        th.start()
    for th in threads:
        th.join()

    return {"results": results, "errors": errors}


# ── Severity extraction ───────────────────────────────────────────────────────

def _count_severity(text: str) -> dict[str, int]:
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for line in text.splitlines():
        for sev in counts:
            if f"[{sev}]" in line:
                counts[sev] += 1
    return counts


def _extract_findings(text: str) -> list[dict]:
    findings = []
    current_sev = None
    current_lines = []

    for line in text.splitlines():
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if line.strip().startswith(f"[{sev}]"):
                if current_sev and current_lines:
                    findings.append({"severity": current_sev, "text": " ".join(current_lines).strip()})
                current_sev = sev
                current_lines = [line.strip()]
                break
        else:
            if current_sev and line.strip():
                current_lines.append(line.strip())

    if current_sev and current_lines:
        findings.append({"severity": current_sev, "text": " ".join(current_lines).strip()})

    return findings


# ── Report generation ─────────────────────────────────────────────────────────

def _render_report(all_results: list[dict], timestamp: str) -> str:
    models_used = set()
    for r in all_results:
        models_used.update(r["per_model"].get("results", {}).keys())

    lines = [
        f"# Governance Code Review — {timestamp[:10]}",
        "",
        f"**Run at**: {timestamp}",
        f"**Models**: {' · '.join(sorted(models_used)) if models_used else 'none (all keys missing)'}",
        f"**Files reviewed**: {len(all_results)}",
        "",
    ]

    # Cross-model consensus summary
    total_by_sev: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    all_model_findings: list[dict] = []

    for r in all_results:
        for model, text in r["per_model"].get("results", {}).items():
            counts = _count_severity(text)
            for sev, n in counts.items():
                total_by_sev[sev] += n
            for finding in _extract_findings(text):
                all_model_findings.append({
                    "file": r["filename"],
                    "model": model,
                    "severity": finding["severity"],
                    "text": finding["text"],
                })

    lines += [
        "## Summary",
        "",
        f"| Severity | Count |",
        f"|---|---|",
        f"| CRITICAL | {total_by_sev['CRITICAL']} |",
        f"| HIGH     | {total_by_sev['HIGH']} |",
        f"| MEDIUM   | {total_by_sev['MEDIUM']} |",
        f"| LOW      | {total_by_sev['LOW']} |",
        "",
    ]

    # Per-file, per-model findings
    for r in all_results:
        lines += [f"---", f"", f"## {r['filename']}", ""]
        for model, text in r["per_model"].get("results", {}).items():
            lines += [f"### {model}", "", text.strip(), ""]
        for model, err in r["per_model"].get("errors", {}).items():
            lines += [f"### {model}", f"", f"_Skipped: {err}_", ""]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    dry_run = "--dry-run" in sys.argv

    # Load .env from the Governance root — real env vars always win (override=False).
    # Copy .env.example to .env and fill in your keys; never commit .env.
    _load_dotenv(_GOV / ".env")

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    timestamp = datetime.now(timezone.utc).isoformat()

    # Report which keys are present
    key_status = []
    key_status.append("gemini-2.0-flash: " + ("OK" if gemini_key else "MISSING (set GEMINI_API_KEY)"))
    key_status.append("gpt-4o: " + ("OK" if openai_key else "MISSING (set OPENAI_API_KEY)"))
    key_status.append("gemma4:12b: Ollama (localhost:11434)")
    print("[governance-review] API key status:")
    for s in key_status:
        print(f"  {s}")

    # Collect files
    review_targets: list[tuple[str, str]] = []
    for filename, fpath in _REVIEW_FILES:
        if not fpath.exists():
            print(f"[governance-review] SKIP (not found): {fpath}")
            continue
        code = fpath.read_text(encoding="utf-8")
        if len(code) > 60_000:
            # Truncate very large files to first 60k chars — LLM context limit
            code = code[:60_000] + "\n\n[...truncated at 60k chars...]"
        review_targets.append((filename, code))

    if dry_run:
        print(f"[governance-review] Dry run — would review {len(review_targets)} files:")
        for name, code in review_targets:
            print(f"  {name} ({len(code)} chars)")
        return 0

    # Run reviews
    all_results = []
    for filename, code in review_targets:
        print(f"[governance-review] Reviewing {filename} ...")
        per_model = _review_file(filename, code, gemini_key, openai_key)
        all_results.append({"filename": filename, "per_model": per_model})
        for model, err in per_model.get("errors", {}).items():
            if "not set" not in err:
                print(f"  [{model}] ERROR: {err}")
        for model in per_model.get("results", {}):
            print(f"  [{model}] done")

    # Write report
    _DOCS.mkdir(parents=True, exist_ok=True)
    report = _render_report(all_results, timestamp)
    _REPORT.write_text(report, encoding="utf-8")
    print(f"[governance-review] Report -> {_REPORT}")

    # Write machine-readable findings JSON (future: feeds Governance_Review_Findings metric)
    findings_flat: list[dict] = []
    for r in all_results:
        for model, text in r["per_model"].get("results", {}).items():
            for f in _extract_findings(text):
                findings_flat.append({
                    "file": r["filename"],
                    "model": model,
                    "severity": f["severity"],
                    "text": f["text"],
                    "reviewed_at": timestamp,
                })
    findings_doc = {
        "reviewed_at": timestamp,
        "total": {
            "CRITICAL": sum(1 for f in findings_flat if f["severity"] == "CRITICAL"),
            "HIGH":     sum(1 for f in findings_flat if f["severity"] == "HIGH"),
            "MEDIUM":   sum(1 for f in findings_flat if f["severity"] == "MEDIUM"),
            "LOW":      sum(1 for f in findings_flat if f["severity"] == "LOW"),
        },
        "findings": findings_flat,
    }
    _JSON.write_text(json.dumps(findings_doc, indent=2), encoding="utf-8")
    print(f"[governance-review] Findings JSON -> {_JSON}")

    # Print summary
    t = findings_doc["total"]
    print(f"\n[governance-review] DONE — {t['CRITICAL']} critical, {t['HIGH']} high, {t['MEDIUM']} medium, {t['LOW']} low")
    if t["CRITICAL"] or t["HIGH"]:
        print(f"[governance-review] ACTION NEEDED — review {_REPORT}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
