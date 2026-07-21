"""Drift-gate: fail on retired paths/endpoints in OPERATIVE config and prompts.

This catches the class of bug that left doctor RED (hardcoded pre-move Desktop paths)
and turns it into a blocking gate, so the next stale literal a daemon or human writes
into a live config or agent prompt fails the gate instead of silently breaking a loop.

Scope is deliberately narrow — only surfaces the runtime actually *reads as instructions*:
  - config/   (JSON the kernel/verifiers consume)
  - prompts/  (agent prompts embedded at dispatch)
Historical prose (docs/, reports/, .quality_register.md) legitimately RECORDS past paths;
scanning it would FAIL on accurate history, so it is intentionally excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import CONFIG_DIR, REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"
SCAN_PATHS = (CONFIG_DIR, PROMPTS_DIR)

TEXT_SUFFIXES = {".json", ".md", ".txt"}

# Migration artifacts that are always wrong in a live operative file:
#  - the pre-move Desktop location (the repo now lives at ~/Agentica-OS)
#  - the retired LM Studio endpoint (local LLM routing migrated to Ollama :11434)
STALE_LITERALS = (
    r"C:\Users\example\Desktop",
    "C:/Users/example/Desktop",
    "localhost:1234",
)


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def _literal_in(content: str, literal: str) -> bool:
    # JSON and source files escape backslashes (C:\\Users\\...), so a single-backslash literal
    # must also be matched in its doubled form or config drift slips through.
    forms = (literal, literal.replace("\\", "\\\\")) if "\\" in literal else (literal,)
    return any(form in content for form in forms)


def scan_stale_literals(
    *,
    scan_paths: Iterable[Path],
    literals: tuple[str, ...],
    base_root: Path = REPO_ROOT,
) -> list[str]:
    offenders: list[str] = []
    base = base_root.resolve()
    for scan_path in scan_paths:
        if not scan_path.exists():
            continue
        files = (
            [scan_path]
            if scan_path.is_file()
            else [p for p in scan_path.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES]
        )
        for file_path in files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            hit = next((lit for lit in literals if _literal_in(content, lit)), None)
            if hit:
                resolved = file_path.resolve()
                try:
                    rel = resolved.relative_to(base).as_posix()
                except ValueError:
                    rel = resolved.as_posix()
                offenders.append(f"{rel} ({hit})")
    return sorted(set(offenders))


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    offenders = scan_stale_literals(scan_paths=SCAN_PATHS, literals=STALE_LITERALS, base_root=repo_root)
    if offenders:
        return [_make_result("FAIL", "stale-path-scan", ", ".join(offenders))]
    return [
        _make_result(
            "OK",
            "stale-path-scan",
            "no retired Desktop/LM-Studio literals in operative config or prompts",
        )
    ]


def main() -> int:
    results = run_checks()
    counts, exit_code = summarize(results)
    for result in results:
        print(f"[{result['status']}] {result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
