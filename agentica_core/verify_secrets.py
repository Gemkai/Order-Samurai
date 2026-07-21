"""Sword pillar — the secret-scanner verifier. Harvested from Jarvis `ag_security_gate.py`
(Step 0 of its daemon) and made platform-agnostic: scans given roots, returns VerifierResult,
and can emit canonical security telemetry into the Data layer (the future unified dashboard's
security feed).

Default scope is bounded to the control plane (governance config + code + Data telemetry) so a
doctor run stays fast — it does NOT walk the Execution junctions (167k+ files) or the Knowledge
vault by default. Point it at other roots explicitly when needed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .types import VerifierResult

_THIS = Path(__file__).resolve()

# (regex, name). generic_hardcoded_secret captures the value in group(2); the rest use group(0).
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"AIzaSy[A-Za-z0-9_-]{33}", "google_gemini_key"),
    (r"sk-or-v1-[A-Za-z0-9_-]{32,}", "openrouter_key"),
    (r"github_pat_[A-Za-z0-9_]{30,}", "github_pat"),
    (r"sk-ant-[A-Za-z0-9_-]{24,}", "anthropic_key"),
    (r"fc-[a-zA-Z0-9]{32,}", "firecrawl_key"),
    (r"sk_test_[a-zA-Z0-9]{20,}", "clerk_test_key"),
    (r"bb_live_[a-zA-Z0-9]{24,}", "browserbase_key"),
    (r"eyJ[a-zA-Z0-9._-]{30,}", "jwt_token"),
    (r"""(?i)(api[_-]?key|secret|token|password)['"]?\s*[:=]\s*['"]([A-Za-z0-9_\-\.]{32,})['"]""",
     "generic_hardcoded_secret"),
]

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".json", ".jsonl", ".md", ".txt",
    ".yaml", ".yml", ".env", ".sh", ".ps1", ".toml", ".cfg", ".ini",
}
EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".obsidian", ".mex",
    "brain", "artifacts", ".tmp", "backups", "file-history", ".pytest_cache",
}
_MAX_BYTES = 2_000_000
_PLACEHOLDER = re.compile(r"\$\{|\$\(|os\.environ|process\.env|<[^>]+>|your[_-]|example|xxxx|placeholder", re.I)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.search(value))


def scan_text(text: str, source: str) -> list[dict]:
    findings: list[dict] = []
    for pattern, name in SECRET_PATTERNS:
        for m in re.finditer(pattern, text):
            value = m.group(2) if name == "generic_hardcoded_secret" else m.group(0)
            if _is_placeholder(value):
                continue
            findings.append({"pattern_name": name, "match_masked": _mask(value), "source": source})
    return findings


def scan_path(root: Path) -> list[dict]:
    findings: list[dict] = []
    if root.is_file():
        files = [root]
    else:
        files = [p for p in root.rglob("*")
                 if p.is_file()
                 and p.suffix.lower() in TEXT_EXTENSIONS
                 and not any(part in EXCLUDE_DIRS for part in p.parts)
                 and p.name != _THIS.name]  # never scan this scanner (its own patterns would match)
    for path in files:
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(text, str(path)))
    return findings


def _default_roots() -> list[Path]:
    governance = _THIS.parents[1]            # Governance
    agentica = governance.parent             # Agentica OS
    os_repo = governance / "Order Samurai"
    return [os_repo / "config", os_repo / "execution", agentica / "Data"]


def run_checks(roots: list[Path] | None = None) -> list[VerifierResult]:
    roots = roots if roots is not None else _default_roots()
    results: list[VerifierResult] = []
    all_findings: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        all_findings.extend(scan_path(root))

    if not all_findings:
        results.append({"status": "OK", "label": "secret-scan",
                        "detail": f"no hardcoded secrets found across {len(roots)} scanned root(s)"})
        return results

    # one FAIL per source file with findings
    by_source: dict[str, list[str]] = {}
    for f in all_findings:
        by_source.setdefault(f["source"], []).append(f"{f['pattern_name']}={f['match_masked']}")
    for source, hits in by_source.items():
        results.append({"status": "FAIL", "label": "secret-scan", "detail": f"{source}: {', '.join(hits)}"})
    return results


def write_log(findings: list[dict], exit_code: int, path: Path | None = None,
              timestamp: str | None = None) -> Path:
    """Append a canonical security event to the Data layer (feeds the unified dashboard)."""
    target = path or (_THIS.parents[2] / "Data" / "telemetry" / "security_gate_log.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "finding_count": len(findings),
        "exit_code": exit_code,
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    return target


def main() -> int:
    from .verifiers import summarize

    results = run_checks()
    for r in results:
        print(f"[{r['status']}] {r['label']}: {r['detail']}")
    counts, exit_code = summarize(results)
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
