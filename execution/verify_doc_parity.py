from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import ANTI_DRIFT_POLICY_PATH, REPO_ROOT

DOC_PARITY_RULE_ID = "docs-move-with-runtime"
SELF_VERIFIER = "execution/verify_doc_parity.py"
PROJECT_DOC_PATH = REPO_ROOT / "PROJECT.md"
RONIN_SPEC_PATH = REPO_ROOT / "RONIN_SPEC.md"

# The human-readable contract clause PROJECT.md must keep in parity with the policy
# principle "Documentation parity is required for architectural changes".
DOCS_MOVE_PRINCIPLE_MARKER = "Docs Move With Runtime"


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


def find_doc_parity_rule(*, payload: dict) -> dict | None:
    return next(
        (rule for rule in payload.get("rules", []) if rule.get("id") == DOC_PARITY_RULE_ID),
        None,
    )


def collect_expected_artifacts(*, payload: dict) -> list[str]:
    artifacts: list[str] = []
    for rule in payload.get("rules", []):
        for artifact in rule.get("expectedArtifacts") or []:
            if artifact not in artifacts:
                artifacts.append(artifact)
    return artifacts


def find_undocumented_artifacts(*, expected_artifacts: list[str], doc_texts: dict[str, str]) -> list[str]:
    combined = "\n".join(doc_texts.values())
    # The human docs are the parity surface themselves, not contract artifacts to be
    # cross-referenced inside their own bodies; exclude them to avoid false positives.
    doc_names = set(doc_texts.keys())
    undocumented: list[str] = []
    for artifact in expected_artifacts:
        if artifact in doc_names:
            continue
        # An artifact is "documented" if its path or basename appears in any human doc.
        basename = Path(artifact).name
        if artifact in combined or basename in combined:
            continue
        undocumented.append(artifact)
    return undocumented


def run_checks(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    project_doc_path = repo_root / "PROJECT.md"

    # 1. The anti-drift policy must load. If it does not, we cannot establish parity
    #    against any declared contract -> FAIL (never silently OK).
    policy_payload, policy_error = _load_json(ANTI_DRIFT_POLICY_PATH)
    if policy_error:
        results.append(_make_result("FAIL", "anti_drift_policy.json", policy_error))
        return results

    # 2. The documentation-parity rule must be declared in the policy.
    doc_rule = find_doc_parity_rule(payload=policy_payload or {})
    if doc_rule is None:
        results.append(
            _make_result(
                "FAIL",
                "anti_drift_policy.json",
                f"missing {DOC_PARITY_RULE_ID} rule",
            )
        )
        return results

    # 3. The rule must route doc parity through THIS verifier. A drifted/renamed
    #    verifier reference is a real architectural gap -> FAIL.
    declared_verifier = doc_rule.get("verifier")
    if declared_verifier != SELF_VERIFIER:
        results.append(
            _make_result(
                "FAIL",
                "anti_drift_policy.json",
                f"{DOC_PARITY_RULE_ID} verifier is {declared_verifier!r}, expected {SELF_VERIFIER!r}",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "anti_drift_policy.json",
                f"{DOC_PARITY_RULE_ID} routes doc parity through {SELF_VERIFIER}",
            )
        )

    # 4. Every doc the rule declares as a required artifact must actually exist.
    declared_docs = list(doc_rule.get("expectedArtifacts") or [])
    if not declared_docs:
        results.append(
            _make_result(
                "FAIL",
                "doc-parity.required-docs",
                f"{DOC_PARITY_RULE_ID} declares no expectedArtifacts",
            )
        )
        return results

    missing_docs = [doc for doc in declared_docs if not (repo_root / doc).is_file()]
    if missing_docs:
        results.append(
            _make_result(
                "FAIL",
                "doc-parity.required-docs",
                "missing required docs: " + ", ".join(sorted(missing_docs)),
            )
        )
        return results
    results.append(
        _make_result(
            "OK",
            "doc-parity.required-docs",
            "required human docs exist: " + ", ".join(declared_docs),
        )
    )

    doc_texts: dict[str, str] = {}
    for doc in declared_docs:
        doc_texts[doc] = (repo_root / doc).read_text(encoding="utf-8", errors="ignore")

    # 5. Parity: every architecture-contract artifact the policy declares must be
    #    referenced in the human docs. A live contract artifact that no doc mentions
    #    is documentation drift (docs did not move with the runtime) -> WARN.
    expected_artifacts = collect_expected_artifacts(payload=policy_payload or {})
    undocumented = find_undocumented_artifacts(
        expected_artifacts=expected_artifacts,
        doc_texts=doc_texts,
    )
    if undocumented:
        results.append(
            _make_result(
                "WARN",
                "doc-parity.artifact-coverage",
                "policy artifacts not referenced in PROJECT.md/RONIN_SPEC.md: "
                + ", ".join(sorted(undocumented)),
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "doc-parity.artifact-coverage",
                "all declared policy artifacts are referenced in the human docs",
            )
        )

    # 6. Parity: PROJECT.md must still carry the documented "Docs Move With Runtime"
    #    contract that mirrors the policy principle. If the docs dropped it, the prose
    #    no longer matches the live contract -> WARN.
    project_text = doc_texts.get("PROJECT.md")
    if project_text is None:
        # PROJECT.md was not among declared docs; read it directly so the check still runs.
        project_text = (
            project_doc_path.read_text(encoding="utf-8", errors="ignore")
            if project_doc_path.is_file()
            else ""
        )
    if DOCS_MOVE_PRINCIPLE_MARKER not in project_text:
        results.append(
            _make_result(
                "WARN",
                "doc-parity.principle",
                f"PROJECT.md no longer documents the {DOCS_MOVE_PRINCIPLE_MARKER!r} contract",
            )
        )
    else:
        results.append(
            _make_result(
                "OK",
                "doc-parity.principle",
                f"PROJECT.md documents the {DOCS_MOVE_PRINCIPLE_MARKER!r} contract",
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
