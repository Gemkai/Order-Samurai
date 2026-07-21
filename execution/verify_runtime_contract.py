from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import execution.runtime_paths as runtime_paths
from execution.runtime_paths import (
    ANTI_DRIFT_POLICY_PATH,
    ANTI_SPRAWL_POLICY_PATH,
    BACKLOG_DIR,
    CONFIG_DIR,
    EXECUTION_DIR,
    PROMOTION_POLICY_PATH,
    REPO_ROOT,
    REPORTS_DIR,
    ROOT_HYGIENE_POLICY_PATH,
    TESTS_DIR,
)

ARCHITECTURE_SCORECARD_PATH = CONFIG_DIR / "architecture_scorecard.json"
PROJECT_DOC_PATH = REPO_ROOT / "PROJECT.md"
RONIN_SPEC_PATH = REPO_ROOT / "RONIN_SPEC.md"
DOCTOR_PATH = EXECUTION_DIR / "doctor.py"
VERIFY_PATH_AUTHORITY_PATH = EXECUTION_DIR / "verify_path_authority.py"
VERIFY_RUNTIME_CONTRACT_PATH = EXECUTION_DIR / "verify_runtime_contract.py"
VERIFY_ROOT_HYGIENE_PATH = EXECUTION_DIR / "verify_root_hygiene.py"
VERIFY_ARCHIVE_BOUNDARIES_PATH = EXECUTION_DIR / "verify_archive_boundaries.py"


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


def build_required_paths() -> dict[str, Path]:
    return {
        "backlog": BACKLOG_DIR,
        "config": CONFIG_DIR,
        "execution": EXECUTION_DIR,
        "reports": REPORTS_DIR,
        "tests": TESTS_DIR,
        "PROJECT.md": PROJECT_DOC_PATH,
        "RONIN_SPEC.md": RONIN_SPEC_PATH,
        "config/architecture_scorecard.json": ARCHITECTURE_SCORECARD_PATH,
        "config/anti_drift_policy.json": ANTI_DRIFT_POLICY_PATH,
        "config/anti_sprawl_policy.json": ANTI_SPRAWL_POLICY_PATH,
        "config/root_hygiene_policy.json": ROOT_HYGIENE_POLICY_PATH,
        "config/promotion_policy.json": PROMOTION_POLICY_PATH,
        "execution/runtime_paths.py": EXECUTION_DIR / "runtime_paths.py",
        "execution/verify_path_authority.py": VERIFY_PATH_AUTHORITY_PATH,
        "execution/verify_runtime_contract.py": VERIFY_RUNTIME_CONTRACT_PATH,
        "execution/verify_root_hygiene.py": VERIFY_ROOT_HYGIENE_PATH,
        "execution/verify_archive_boundaries.py": VERIFY_ARCHIVE_BOUNDARIES_PATH,
        "execution/doctor.py": DOCTOR_PATH,
        "tests/test_verify_path_authority.py": TESTS_DIR / "test_verify_path_authority.py",
        "tests/test_verify_runtime_contract.py": TESTS_DIR / "test_verify_runtime_contract.py",
        "tests/test_verify_root_hygiene.py": TESTS_DIR / "test_verify_root_hygiene.py",
        "tests/test_verify_archive_boundaries.py": TESTS_DIR / "test_verify_archive_boundaries.py",
    }


def validate_required_paths(*, required_paths: dict[str, Path]) -> list[str]:
    failures: list[str] = []

    for label, path in required_paths.items():
        expects_file = bool(path.suffix)
        if expects_file and (not path.exists() or not path.is_file()):
            failures.append(label)
        if not expects_file and (not path.exists() or not path.is_dir()):
            failures.append(label)

    return sorted(failures)


def validate_runtime_paths_stay_under_root(*, path_module: ModuleType, repo_root: Path) -> list[str]:
    failures: list[str] = []
    root_resolved = repo_root.resolve()

    for name, value in vars(path_module).items():
        if name.startswith("_") or name != name.upper() or not isinstance(value, Path):
            continue

        resolved = value.resolve(strict=False)
        if resolved == root_resolved:
            continue

        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            failures.append(name)

    return sorted(failures)


def validate_runtime_scorecard_contract(*, payload: dict) -> list[str]:
    failures: list[str] = []
    runtime_coherence = next(
        (category for category in payload.get("categories", []) if category.get("id") == "runtime_coherence"),
        None,
    )

    if runtime_coherence is None:
        return ["missing runtime_coherence category"]

    required_artifacts = set(runtime_coherence.get("requiredArtifacts") or [])
    required_verifiers = set(runtime_coherence.get("requiredVerifiers") or [])

    if "execution/doctor.py" not in required_artifacts:
        failures.append("runtime_coherence missing execution/doctor.py")
    if "config/anti_drift_policy.json" not in required_artifacts:
        failures.append("runtime_coherence missing config/anti_drift_policy.json")
    if "execution/verify_runtime_contract.py" not in required_verifiers:
        failures.append("runtime_coherence missing execution/verify_runtime_contract.py")

    return failures


def validate_runtime_policy_contract(*, payload: dict) -> list[str]:
    failures: list[str] = []
    doctor_rule = next(
        (rule for rule in payload.get("rules", []) if rule.get("id") == "doctor-is-the-operator-entrypoint"),
        None,
    )

    if doctor_rule is None:
        return ["missing doctor-is-the-operator-entrypoint rule"]

    expected_artifacts = set(doctor_rule.get("expectedArtifacts") or [])
    if "execution/doctor.py" not in expected_artifacts:
        failures.append("doctor rule missing execution/doctor.py")
    if doctor_rule.get("verifier") != "execution/verify_runtime_contract.py":
        failures.append("doctor rule missing execution/verify_runtime_contract.py")

    return failures


def validate_doctor_entrypoint(*, doctor_path: Path) -> list[str]:
    try:
        content = doctor_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ["missing execution/doctor.py"]

    expected_snippets = {
        "execution.verify_path_authority": "verify_path_authority",
        "execution.verify_runtime_contract": "verify_runtime_contract",
        "execution.verify_root_hygiene": "verify_root_hygiene",
        "execution.verify_archive_boundaries": "verify_archive_boundaries",
        "Summary: OK=": "summary output",
    }

    failures = [
        label
        for snippet, label in expected_snippets.items()
        if snippet not in content
    ]
    return sorted(failures)


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    missing_paths = validate_required_paths(required_paths=build_required_paths())
    if missing_paths:
        results.append(_make_result("FAIL", "runtime-contract.artifacts", ", ".join(missing_paths)))
    else:
        results.append(
            _make_result(
                "OK",
                "runtime-contract.artifacts",
                "required runtime artifacts exist across config, execution, docs, and tests",
            )
        )

    path_failures = validate_runtime_paths_stay_under_root(path_module=runtime_paths, repo_root=repo_root)
    if path_failures:
        results.append(_make_result("FAIL", "runtime_paths.py", ", ".join(path_failures)))
    else:
        results.append(
            _make_result(
                "OK",
                "runtime_paths.py",
                "canonical runtime paths resolve under the project root",
            )
        )

    scorecard_payload, scorecard_error = _load_json(ARCHITECTURE_SCORECARD_PATH)
    if scorecard_error:
        results.append(_make_result("FAIL", "architecture_scorecard.json", scorecard_error))
    else:
        scorecard_failures = validate_runtime_scorecard_contract(payload=scorecard_payload or {})
        if scorecard_failures:
            results.append(
                _make_result(
                    "FAIL",
                    "architecture_scorecard.json",
                    ", ".join(scorecard_failures),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "architecture_scorecard.json",
                    "runtime coherence category declares the doctor entrypoint and runtime-contract verifier",
                )
            )

    anti_drift_payload, anti_drift_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if anti_drift_error:
        results.append(_make_result("FAIL", "anti_drift_policy.json", anti_drift_error))
    else:
        anti_drift_failures = validate_runtime_policy_contract(payload=anti_drift_payload or {})
        if anti_drift_failures:
            results.append(
                _make_result(
                    "FAIL",
                    "anti_drift_policy.json",
                    ", ".join(anti_drift_failures),
                )
            )
        else:
            results.append(
                _make_result(
                    "OK",
                    "anti_drift_policy.json",
                    "anti-drift policy routes doctor coherence through verify_runtime_contract.py",
                )
            )

    doctor_failures = validate_doctor_entrypoint(doctor_path=DOCTOR_PATH)
    if doctor_failures:
        results.append(_make_result("FAIL", "doctor.py", ", ".join(doctor_failures)))
    else:
        results.append(
            _make_result(
                "OK",
                "doctor.py",
                "doctor aggregates the active verifiers and emits a machine-readable summary line",
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