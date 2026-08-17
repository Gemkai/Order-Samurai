"""Runtime-coupling gate for the live Claude home (backlog item 9).

Fails when live Claude runtime surfaces reference Antigravity-owned paths,
backup roots, or other forbidden historical surfaces. Consumes the
boundaryRules of config/claude_root_hygiene_policy.json (scan paths,
forbiddenPatterns, forbiddenRoots) and reads config/claude_anti_drift_policy.json
only for context (the runtime-coupling-boundary rule severity).

Matching rules (deliberate, calibrated against the live runtime):
  - forbiddenPatterns are plain SUBSTRINGS matched via ``_literal_in`` (ported
    from execution/verify_no_stale_paths.py), which also matches the JSON
    doubled-backslash form of any backslash literal. They are NEVER compiled
    as regexes — the retained Windows entries contain ``\\U...`` escapes that
    crash ``re.compile``.
  - forbiddenRoots ("backups", "file-history") flag scanned file CONTENT that
    references an entry under that root of the runtime home. The needle is
    anchored to the Claude home (``.claude/<root>/``, the backslash form, and
    the resolved runtime root's absolute form) rather than the bare
    ``<root>/`` substring: calibration against the live runtime showed the
    unanchored form false-positives on unrelated prose (e.g.
    skills/pp-digitalocean/SKILL.md discussing DigitalOcean droplet
    "backups/").

Self-reference exclusion: files whose basename matches one of the enforcement
pack's own policy files (claude_root_hygiene_policy.json et al.) are skipped —
a copy of the policy inside a scan path would otherwise flag its own
forbiddenPatterns declarations.

The scan of the live home is read-only and bounded: only the policy's
scanPaths are visited, directories named in SKIP_DIR_NAMES are pruned, files
are capped at 1MB (64KB when extensionless), and only text-like extensions
(plus small extensionless files) are read. A scan path missing on this
machine is a WARN, never a crash.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterator

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.verifier_results import make_result as _make_result  # noqa: F401
from execution.verifier_results import summarize  # noqa: F401  (re-exported for doctor/CLI)

from execution.claude_runtime_target import (
    ALL_POLICY_PATHS,
    ANTI_DRIFT_POLICY_PATH,
    ROOT_HYGIENE_POLICY_PATH,
    runtime_root,
)

SKIP_DIR_NAMES = {
    "node_modules",
    ".git",
    "backups",
    "file-history",
    "projects",
    "shell-snapshots",
    ".tmp",
}
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".js", ".ts"}
MAX_FILE_BYTES = 1_000_000
EXTENSIONLESS_MAX_BYTES = 65_536

# See module docstring: skip the pack's own policy files if copies sit inside
# a scan path — they declare the forbidden strings and would flag themselves.
SELF_REFERENCE_BASENAMES = frozenset(path.name for path in ALL_POLICY_PATHS)

ANTI_DRIFT_COUPLING_RULE_ID = "runtime-coupling-boundary"


def _literal_in(content: str, literal: str) -> bool:
    # Ported from execution/verify_no_stale_paths.py: JSON and source files
    # escape backslashes (C:\\Users\\...), so a single-backslash literal must
    # also be matched in its doubled form or config drift slips through.
    forms = (literal, literal.replace("\\", "\\\\")) if "\\" in literal else (literal,)
    return any(form in content for form in forms)


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _normalize_entry(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _forbidden_root_needles(root_name: str, runtime: Path) -> tuple[str, ...]:
    """Content needles marking a reference to an entry under a forbidden root
    of the runtime home. Anchored to the Claude home — see module docstring
    for why the bare '<root>/' substring is not used."""
    clean = _normalize_entry(root_name)
    if not clean:
        return ()
    windows_form = clean.replace("/", "\\")
    return (
        f".claude/{clean}/",
        ".claude\\" + windows_form + "\\",
        f"{runtime.as_posix()}/{clean}/",
    )


def _is_scannable(path: Path) -> bool:
    if path.name in SELF_REFERENCE_BASENAMES:
        return False
    if path.is_symlink():
        return False
    suffix = path.suffix.lower()
    if suffix and suffix not in TEXT_SUFFIXES:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return size <= (MAX_FILE_BYTES if suffix else EXTENSIONLESS_MAX_BYTES)


def _iter_candidate_files(scan_target: Path) -> Iterator[Path]:
    if scan_target.is_file():
        yield scan_target
        return
    for dirpath, dirnames, filenames in os.walk(scan_target):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIR_NAMES]
        for filename in filenames:
            yield Path(dirpath) / filename


def _allowlist_paths(rule: dict) -> dict[str, str]:
    """Normalized {path: reason} of intentional, documented references excused
    from this rule. Governance pattern #3 (intentional_scope): a coupling that
    IS the file's purpose (e.g. retention_reaper.py's cross-home GC) or a bare
    provenance comment is not drift — declaring it here stops cry-wolf false
    positives that train the reader to ignore the gate. Each entry carries a
    reason string for auditability."""
    out: dict[str, str] = {}
    for entry in rule.get("allowlist") or []:
        path = _normalize_entry(entry.get("path", ""))
        if path:
            out[path] = str(entry.get("reason", "")).strip()
    return out


def scan_boundary_rule(*, rule: dict, runtime: Path) -> tuple[list[str], list[str], int, int]:
    """Scan one boundaryRule. Returns
    (offenders, missing_scan_paths, scanned_count, allowlisted_count).

    Offender entries carry the path plus the violating pattern/root, e.g.
    "scripts/retention_reaper.py (pattern: ~/.gemini/antigravity)".
    Files named in the rule's allowlist are excused (counted, not offending).
    """
    needles: list[tuple[str, str]] = []
    for pattern in rule.get("forbiddenPatterns") or []:
        if pattern:
            needles.append((pattern, f"pattern: {pattern}"))
    for root_name in rule.get("forbiddenRoots") or []:
        for needle in _forbidden_root_needles(root_name, runtime):
            needles.append((needle, f"root: {_normalize_entry(root_name)}"))

    allowlist = _allowlist_paths(rule)
    offenders: set[str] = set()
    allowlisted: set[str] = set()
    missing: list[str] = []
    scanned = 0
    runtime_resolved = runtime.resolve()

    for raw_scan_path in rule.get("scanPaths") or []:
        normalized = _normalize_entry(raw_scan_path)
        if not normalized:
            continue
        target = runtime / normalized
        if not target.exists():
            missing.append(normalized)
            continue
        for file_path in _iter_candidate_files(target):
            if not _is_scannable(file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            tag = next((tag for needle, tag in needles if _literal_in(content, needle)), None)
            if tag:
                resolved = file_path.resolve()
                try:
                    rel = resolved.relative_to(runtime_resolved).as_posix()
                except ValueError:
                    rel = resolved.as_posix()
                if _normalize_entry(rel) in allowlist:
                    allowlisted.add(rel)
                    continue
                offenders.add(f"{rel} ({tag})")

    return sorted(offenders), missing, scanned, len(allowlisted)


def _anti_drift_context_result(anti_drift_path: Path) -> dict[str, str]:
    payload, error = _load_json(anti_drift_path)
    if error:
        return _make_result("WARN", "claude_anti_drift_policy.json", f"context unavailable ({error})")
    rule = next(
        (item for item in (payload or {}).get("rules", []) if item.get("id") == ANTI_DRIFT_COUPLING_RULE_ID),
        None,
    )
    if rule is None:
        return _make_result(
            "WARN",
            "claude_anti_drift_policy.json",
            f"context rule {ANTI_DRIFT_COUPLING_RULE_ID} not declared",
        )
    return _make_result(
        "OK",
        "claude_anti_drift_policy.json",
        f"context: {ANTI_DRIFT_COUPLING_RULE_ID} severity={rule.get('severity', 'unspecified')}",
    )


def run_checks(
    *,
    policy_path: Path = ROOT_HYGIENE_POLICY_PATH,
    anti_drift_path: Path = ANTI_DRIFT_POLICY_PATH,
    root: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    runtime = root if root is not None else runtime_root()

    policy_payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(_make_result("FAIL", "claude_root_hygiene_policy.json", policy_error))
        return results

    results.append(_anti_drift_context_result(anti_drift_path))

    boundary_rules = (policy_payload or {}).get("boundaryRules") or []
    if not boundary_rules:
        results.append(
            _make_result(
                "WARN",
                "claude_root_hygiene_policy.json",
                "no boundaryRules declared — nothing to enforce",
            )
        )
        return results

    if not runtime.exists():
        results.append(
            _make_result(
                "WARN",
                "runtime_coupling.root",
                f"runtime root missing on this machine: {runtime} — scan skipped",
            )
        )
        return results

    for rule in boundary_rules:
        rule_name = rule.get("name", "unnamed")
        label = f"runtime_coupling.{rule_name}"
        if not (rule.get("forbiddenPatterns") or rule.get("forbiddenRoots")):
            results.append(
                _make_result("WARN", label, "declares neither forbiddenPatterns nor forbiddenRoots")
            )
            continue

        offenders, missing, scanned, allowlisted = scan_boundary_rule(rule=rule, runtime=runtime)
        if missing:
            results.append(
                _make_result(
                    "WARN",
                    f"{label}.scan-paths",
                    f"missing under {runtime}: {', '.join(missing)}",
                )
            )
        allow_note = f" ({allowlisted} intentional ref(s) allowlisted)" if allowlisted else ""
        if offenders:
            results.append(_make_result("FAIL", label, ", ".join(offenders)))
        else:
            results.append(
                _make_result(
                    "OK",
                    label,
                    f"no forbidden references in {scanned} scanned files{allow_note}",
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
