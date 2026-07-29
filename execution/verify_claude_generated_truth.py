"""Generated-truth freshness gate for the live Claude home (backlog item 5).

Verifies that the runtime's *generated* truth artifacts remain the authority
for existence questions: they are present, structurally valid, kept fresh
relative to the script that generates them, and not impersonated by a
handwritten document.

Consumes config/claude_anti_drift_policy.json. The set of generated artifacts
and each artifact's generator are DERIVED from the policy's generation rules
(any rule whose expectedRuntimeArtifacts declare a sync_*.py generator plus one
or more non-.py outputs) rather than hardcoded here. On this machine those
rules yield:

    settings.json                <- scripts/sync_settings_config.py
    mcp.json                     <- scripts/sync_mcp_config.py
    data/runtime_inventory.json  <- scripts/sync_runtime_inventory.py
    data/runtime_summary.md      <- scripts/sync_runtime_inventory.py

Calibration (deliberate, per spec):
  - A MISSING generated artifact is a WARN, not a FAIL — the generator simply
    has not run yet; it is advisory, not a broken contract.
  - A PRESENT-but-malformed JSON artifact (e.g. a settings.json that will not
    parse) is a FAIL — stale/handwritten truth has replaced generated truth.
  - Freshness is a read-only mtime comparison: WARN when a generated output is
    older than its generator script. Skipped when either side is absent.
  - Impersonation is best-effort and conservative: only when the generated
    data/runtime_inventory.json is absent do we look for a handwritten .md
    under the runtime root that declares ITSELF the runtime inventory. It only
    ever WARNs, never fabricates a FAIL.

All filesystem access is read-only and bounded. A missing runtime root or a
missing artifact is a WARN, never a crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.claude_runtime_target import ANTI_DRIFT_POLICY_PATH, runtime_root

EXPECTED_VERIFIER = "execution/verify_claude_generated_truth.py"
INVENTORY_RULE_ID = "generated-runtime-inventory"

# Bounds for the conservative impersonation scan.
IMPERSONATION_SCAN_SUBDIRS = ("", "data")
IMPERSONATION_MAX_BYTES = 262_144
# A handwritten doc impersonates the inventory only when it both talks about a
# "runtime inventory" AND claims to BE the authority — keeps the scan
# conservative so ordinary prose mentioning an inventory does not false-positive.
IMPERSONATION_ANCHOR = "runtime inventory"
IMPERSONATION_CLAIM_PHRASES = (
    "authoritative",
    "source of truth",
    "canonical inventory",
    "runtime existence",
)


def _make_result(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def summarize(results: list[dict[str, str]]) -> tuple[dict[str, int], int]:
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return counts, 1 if counts["FAIL"] else 0


def _load_json(path: Path) -> tuple[dict | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _normalize_artifact(value: str) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def derive_generated_artifacts(payload: dict) -> list[tuple[str, str]]:
    """Derive (output, generator) pairs from the policy's generation rules.

    A generation rule declares a sync_*.py generator plus one or more non-.py
    outputs in expectedRuntimeArtifacts. Rules without such a generator (path
    authority, doctor, verification) are ignored. Order is stable and
    deduplicated on the output path.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rule in payload.get("rules", []):
        artifacts = [
            _normalize_artifact(entry)
            for entry in rule.get("expectedRuntimeArtifacts", [])
        ]
        artifacts = [entry for entry in artifacts if entry]
        generator = next(
            (
                entry
                for entry in artifacts
                if entry.endswith(".py") and "sync" in Path(entry).name
            ),
            None,
        )
        if not generator:
            continue
        for entry in artifacts:
            if entry.endswith(".py"):
                continue
            if entry in seen:
                continue
            seen.add(entry)
            pairs.append((entry, generator))
    return pairs


def _rule_wiring_result(payload: dict) -> dict[str, str]:
    rule = next(
        (item for item in payload.get("rules", []) if item.get("id") == INVENTORY_RULE_ID),
        None,
    )
    if rule is None:
        return _make_result(
            "WARN",
            "generated_truth.rule-wiring",
            f"anti-drift policy does not declare the {INVENTORY_RULE_ID} rule",
        )
    if rule.get("verifier") != EXPECTED_VERIFIER:
        return _make_result(
            "WARN",
            "generated_truth.rule-wiring",
            f"{INVENTORY_RULE_ID} verifier is {rule.get('verifier')!r}, expected {EXPECTED_VERIFIER!r}",
        )
    return _make_result(
        "OK",
        "generated_truth.rule-wiring",
        f"{INVENTORY_RULE_ID} routes through {EXPECTED_VERIFIER}",
    )


def _check_artifact_presence(output_path: Path, artifact: str) -> dict[str, str]:
    label = f"generated_truth.{artifact}"
    if not output_path.exists():
        return _make_result(
            "WARN",
            label,
            "generated artifact not present yet (advisory — generator has not run)",
        )
    if artifact.endswith(".json"):
        _, error = _load_json(output_path)
        if error:
            return _make_result("FAIL", label, f"present but {error}")
        return _make_result("OK", label, "present and parses as JSON")
    try:
        output_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return _make_result("WARN", label, f"present but unreadable: {exc}")
    return _make_result("OK", label, "present")


def _check_freshness(output_path: Path, generator_path: Path, artifact: str) -> dict[str, str] | None:
    if not output_path.exists() or not generator_path.exists():
        return None
    try:
        output_mtime = output_path.stat().st_mtime
        generator_mtime = generator_path.stat().st_mtime
    except OSError:
        return None
    if output_mtime < generator_mtime:
        return _make_result(
            "WARN",
            f"generated_truth.{artifact}.freshness",
            f"stale: output older than its generator ({generator_path.name}) — regenerate",
        )
    return None


def _scan_for_impersonation(runtime: Path, generated_basenames: set[str]) -> list[str]:
    offenders: list[str] = []
    for subdir in IMPERSONATION_SCAN_SUBDIRS:
        directory = runtime / subdir if subdir else runtime
        if not directory.is_dir():
            continue
        for md_path in sorted(directory.glob("*.md")):
            if md_path.name in generated_basenames:
                continue
            try:
                if md_path.stat().st_size > IMPERSONATION_MAX_BYTES:
                    continue
                content = md_path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if IMPERSONATION_ANCHOR not in content:
                continue
            if any(phrase in content for phrase in IMPERSONATION_CLAIM_PHRASES):
                try:
                    rel = md_path.resolve().relative_to(runtime.resolve()).as_posix()
                except ValueError:
                    rel = md_path.name
                offenders.append(rel)
    return offenders


def _impersonation_result(
    runtime: Path,
    artifacts: list[tuple[str, str]],
    inventory_present: bool,
) -> dict[str, str]:
    label = "generated_truth.impersonation"
    if inventory_present:
        return _make_result(
            "OK",
            label,
            "generated runtime inventory is present; no impersonation possible",
        )
    generated_basenames = {Path(artifact).name for artifact, _ in artifacts}
    offenders = _scan_for_impersonation(runtime, generated_basenames)
    if offenders:
        return _make_result(
            "WARN",
            label,
            "handwritten doc claims to be the runtime inventory while the "
            f"generated data/runtime_inventory.json is absent: {', '.join(offenders)}",
        )
    return _make_result(
        "OK",
        label,
        "no handwritten doc impersonates the generated runtime inventory",
    )


def run_checks(
    *,
    policy_path: Path = ANTI_DRIFT_POLICY_PATH,
    runtime_root_dir: Path | None = None,
) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    runtime = runtime_root_dir if runtime_root_dir is not None else runtime_root()

    policy_payload, policy_error = _load_json(policy_path)
    if policy_error:
        results.append(_make_result("FAIL", "claude_anti_drift_policy.json", policy_error))
        return results

    results.append(
        _make_result(
            "OK",
            "claude_anti_drift_policy.json",
            "anti-drift policy loaded",
        )
    )
    results.append(_rule_wiring_result(policy_payload or {}))

    artifacts = derive_generated_artifacts(policy_payload or {})
    if not artifacts:
        results.append(
            _make_result(
                "WARN",
                "generated_truth.artifacts",
                "policy declares no generated-truth artifacts to verify",
            )
        )
        return results

    if not runtime.exists():
        results.append(
            _make_result(
                "WARN",
                "generated_truth.root",
                f"runtime root missing on this machine: {runtime} — checks skipped",
            )
        )
        return results

    inventory_present = False
    for artifact, generator in artifacts:
        output_path = runtime / artifact
        generator_path = runtime / generator

        results.append(_check_artifact_presence(output_path, artifact))
        if artifact == "data/runtime_inventory.json" and output_path.exists():
            inventory_present = True

        freshness = _check_freshness(output_path, generator_path, artifact)
        if freshness is not None:
            results.append(freshness)

    results.append(_impersonation_result(runtime, artifacts, inventory_present))

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
