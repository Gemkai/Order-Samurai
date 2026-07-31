"""Path-authority gate for the LIVE Claude runtime (~/.claude), backlog item 2.

Enforces the anti-drift policy's `single-path-authority` rule: no live Claude
runtime module may hardcode Claude-home or Antigravity-owned ABSOLUTE machine
paths outside the canonical path authority. Portable self-references
(Path.home() / ".claude", ~ expansion) are fine — only literal absolute paths
drift when the runtime moves hosts. Tilde-form Antigravity references are the
runtime-coupling-boundary rule's jurisdiction (verify_claude_runtime_coupling),
not this gate's.

Scope is the runtime CODE surfaces the policy covers (hooks/, llm/,
orchestration/, safety/, scripts/) under `claude_runtime_target.runtime_root()`
— which honors CLAUDE_RUNTIME_ROOT for tests/sandboxes. The scan is bounded and
read-only: vendor/history/state directories are pruned, files are capped at
1MB, and only text-like extensions (plus small extensionless files) are read.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import (  # noqa: E402
    ANTI_DRIFT_POLICY_PATH,
    pinned_home_paths,
    runtime_root,
)

PATH_AUTHORITY_RULE_ID = "single-path-authority"
EXPECTED_VERIFIER = "execution/verify_claude_path_authority.py"

# Runtime *code* surfaces under the Claude home (per the policy scope: "All live
# Claude runtime ... surfaces"). Prompt/data surfaces (commands/, skills/,
# data/, ...) are out of scope here — this gate is about code hardcoding paths.
SCAN_SURFACES = ("hooks", "llm", "orchestration", "safety", "scripts")

# Never descended into: vendor, history, and state trees are large and are not
# live runtime code.
SKIP_DIR_NAMES = frozenset(
    {"node_modules", ".git", "backups", "file-history", "projects", "shell-snapshots", ".tmp"}
)
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".js", ".ts"}
MAX_FILE_BYTES = 1_000_000

#: Runtime homes that must never appear as an ABSOLUTE path in the scanned
#: surfaces, POSIX-spelled. These were tuples of literal paths carrying this
#: machine's own home until 2026-07-29; the public exporter rewrote
#: "/Users/<owner>/.claude" to "~/.claude", turning a denylist entry into the
#: portable form this verifier exists to accept. See the matcher's note in
#: claude_runtime_target. A leading "." marks a runtime dir (pattern-matched
#: over any user's home); anything else is matched as a plain literal.
FORBIDDEN_RUNTIME_DIRS = (".claude", ".gemini/antigravity")


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


def find_path_authority_rule(payload: dict) -> dict | None:
    for rule in payload.get("rules", []):
        if rule.get("id") == PATH_AUTHORITY_RULE_ID:
            return rule
    return None


def forbidden_literals(policy_payload: dict) -> tuple[str, ...]:
    """The forbidden runtime dirs, plus an ABSOLUTE root the policy declares.

    The declared root used to be appended unconditionally. It must not be: the
    policy's own `targetRuntimeRoot` is the portable "~/.claude" (it was this
    machine's absolute home until 2026-07-31, and the exporter scrubbed it to
    the same portable form), so a verbatim append put the form this verifier
    exists to ACCEPT onto the denylist and inverted the scan. An absolute
    declared root is still worth forbidding — including one outside /Users and
    /home, which the pattern cannot know about. Nothing is lost by skipping the
    "~" form: FORBIDDEN_RUNTIME_DIRS already matches any user's home-rooted
    .claude by pattern, which is strictly wider than the old literal.
    """
    entries: list[str] = list(FORBIDDEN_RUNTIME_DIRS)
    declared_root = str(policy_payload.get("targetRuntimeRoot") or "").strip()
    if declared_root and (declared_root.startswith("/") or re.match(r"^[A-Za-z]:", declared_root)):
        entries.append(declared_root)
    return tuple(dict.fromkeys(entries))


def default_allowlist(rule: dict) -> frozenset[str]:
    """Approved bridge files, relative to the runtime root.

    The rule statement forbids hardcoding "outside the canonical path
    authority", so the authority the policy declares (expectedRuntimeArtifacts:
    scripts/runtime_paths.py) is the default — and, deliberately, the entire —
    escape hatch, plus any explicitly declared bridges.
    """
    declared = list(rule.get("expectedRuntimeArtifacts") or [])
    declared += list(rule.get("allowedBridges") or [])
    return frozenset(
        str(entry).strip().replace("\\", "/").strip("/") for entry in declared if str(entry).strip()
    )


def _literal_in(content: str, literal: str) -> bool:
    # JSON and source files escape backslashes (C:\\Users\\...), so a
    # single-backslash literal must also be matched in its doubled form or
    # config drift slips through. NEVER re.compile a raw literal — the Windows
    # forms contain \U sequences that crash re; the runtime-dir entries go
    # through the pre-built patterns in claude_runtime_target instead.
    forms = (literal, literal.replace("\\", "\\\\")) if "\\" in literal else (literal,)
    return any(form in content for form in forms)


def _hits_in(content: str, entries: tuple[str, ...]) -> list[str]:
    """Offending absolute paths in `content`, one canonical spelling each.

    A leading "." marks a runtime dir matched by pattern over ANY user's home;
    anything else is an explicit absolute root matched literally.
    """
    dirs = tuple(e for e in entries if e.startswith("."))
    hits = set(pinned_home_paths(content, *dirs)) if dirs else set()
    hits.update(e for e in entries if not e.startswith(".") and _literal_in(content, e))
    return sorted(hits)


def _is_text_like(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return True
    # Extensionless runtime files (launchers, shims) stay in scope; the size
    # cap below keeps this bounded.
    return suffix == ""


def _iter_scannable_files(surface_dir: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(surface_dir):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        for filename in filenames:
            file_path = Path(dirpath) / filename
            if not _is_text_like(file_path):
                continue
            try:
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield file_path


def scan_path_literals(
    *,
    root: Path,
    literals: tuple[str, ...],
    surfaces: Iterable[str] = SCAN_SURFACES,
    allowlist: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Return (offenders, missing_surfaces).

    offenders: "surface-relative/path (matched literal, ...)" entries, sorted.
    missing_surfaces: declared scan surfaces absent under root (WARN material —
    a partial runtime on this machine is not a violation).
    """
    offenders: list[str] = []
    missing_surfaces: list[str] = []
    for surface in surfaces:
        surface_dir = root / surface
        if not surface_dir.is_dir():
            missing_surfaces.append(surface)
            continue
        for file_path in _iter_scannable_files(surface_dir):
            rel = file_path.relative_to(root).as_posix()
            if rel in allowlist:
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            hits = _hits_in(content, literals)
            if hits:
                offenders.append(f"{rel} ({', '.join(hits)})")
    return sorted(set(offenders)), missing_surfaces


def run_checks(
    runtime_root_path: Path | None = None,
    allowlist: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    policy_payload, policy_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "claude_anti_drift_policy.json", policy_error))
        return results

    rule = find_path_authority_rule(policy_payload or {})
    if rule is None:
        results.append(
            _make_result(
                "FAIL",
                "claude_anti_drift_policy.json",
                f"missing {PATH_AUTHORITY_RULE_ID} rule",
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "claude_anti_drift_policy.json",
            f"path authority rule '{PATH_AUTHORITY_RULE_ID}' declared",
        )
    )

    if rule.get("verifier") != EXPECTED_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                "claude-path-authority.verifier-wiring",
                f"rule verifier is {rule.get('verifier')!r}, expected {EXPECTED_VERIFIER!r}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-path-authority.verifier-wiring",
                f"rule routes through {EXPECTED_VERIFIER}",
            )
        )

    live_root = runtime_root_path if runtime_root_path is not None else runtime_root()
    if not live_root.is_dir():
        results.append(
            _make_result(
                "WARN",
                "claude-path-authority.runtime-root",
                f"runtime root not present on this machine: {live_root}",
            )
        )
        return results

    effective_allowlist = (
        frozenset(str(entry).strip().replace("\\", "/").strip("/") for entry in allowlist)
        if allowlist is not None
        else default_allowlist(rule)
    )
    offenders, missing_surfaces = scan_path_literals(
        root=live_root,
        literals=forbidden_literals(policy_payload or {}),
        allowlist=effective_allowlist,
    )

    if missing_surfaces:
        results.append(
            _make_result(
                "WARN",
                "claude-path-authority.scan-surfaces",
                "scan surface(s) missing on this machine: " + ", ".join(missing_surfaces),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "claude-path-authority.scan-surfaces",
                "all runtime code surfaces present: " + ", ".join(SCAN_SURFACES),
            )
        )

    if offenders:
        results.append(
            _make_result(
                "FAIL",
                "claude-path-authority.literal-scan",
                "hardcoded absolute runtime paths outside the path authority: " + ", ".join(offenders),
            )
        )
    else:
        allow_note = ", ".join(sorted(effective_allowlist)) or "<none>"
        results.append(
            _make_result(
                "OK",
                "claude-path-authority.literal-scan",
                "no literal Claude-home/Antigravity absolute paths outside the path authority "
                f"(allowlist: {allow_note})",
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
