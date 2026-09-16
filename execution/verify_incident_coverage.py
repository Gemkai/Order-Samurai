#!/usr/bin/env python3
"""Find recent incident knowledge that has no resolvable detection instrument."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[3]
OS_ROOT = Path(__file__).resolve().parents[1]
if str(OS_ROOT) not in sys.path:
    sys.path.insert(0, str(OS_ROOT))
MEMORY_ROOT = (
    Path.home() / ".claude" / "projects"
    / "-Users-exampleuser-AgenticaOS" / "memory"
)
SOLUTION_ROOTS = (
    REPO_ROOT / "docs" / "solutions",
    REPO_ROOT / "Governance" / "docs" / "solutions",
    OS_ROOT / "docs" / "solutions",
)
VALID_KINDS = {"metric", "verifier", "doctor_family", "hook", "git_hook"}


@dataclass
class Catalog:
    metrics: set[str] = field(default_factory=set)
    verifiers: set[str] = field(default_factory=set)
    doctor_families: set[str] = field(default_factory=set)
    hooks: set[str] = field(default_factory=set)
    git_hooks: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def _scalar(raw: str):
    value = raw.strip()
    if not value:
        return None
    if value in {"null", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(path: Path) -> dict:
    """Parse the small YAML subset used by solution and memory headers."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out: dict = {}
    section: str | None = None
    for line in text[4:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and ":" in stripped:
            key, raw = stripped.split(":", 1)
            value = _scalar(raw)
            if value is None:
                section = key
                out[key] = [] if key == "covered_by" else {}
            else:
                section = None
                out[key] = value
            continue
        if section == "covered_by" and stripped.startswith("- "):
            key, raw = stripped[2:].split(":", 1)
            out[section].append({key: _scalar(raw)})
        elif section == "covered_by" and ":" in stripped and out[section]:
            key, raw = stripped.split(":", 1)
            out[section][-1][key] = _scalar(raw)
        elif section and isinstance(out.get(section), dict) and ":" in stripped:
            key, raw = stripped.split(":", 1)
            out[section][key] = _scalar(raw)
    return out


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_catalog(repo_root: Path = REPO_ROOT) -> Catalog:
    catalog = Catalog()
    governance = repo_root / "Governance"
    if str(governance) not in sys.path:
        sys.path.insert(0, str(governance))
    try:
        from agentica_core import insights
        catalog.metrics = set(insights.METRIC_CONFIG)
    except (ImportError, AttributeError) as exc:
        catalog.errors.append(f"metric registry unreadable: {exc}")

    execution = repo_root / "Governance" / "Order Samurai" / "execution"
    catalog.verifiers = {path.stem for path in execution.glob("verify_*.py")}
    try:
        from execution import doctor
        catalog.doctor_families = {family.name for family in doctor.CHECK_FAMILIES}
    except (ImportError, AttributeError) as exc:
        catalog.errors.append(f"doctor registry unreadable: {exc}")

    hook_registry = Path.home() / ".claude" / "scripts" / "hook_registry.py"
    try:
        hooks = _load_module(hook_registry, "incident_coverage_hook_registry")
        catalog.hooks = set(hooks.HOOKS)
    except (OSError, ImportError, AttributeError) as exc:
        catalog.errors.append(f"hook registry unreadable: {exc}")

    hook_dir = repo_root / ".githooks"
    if hook_dir.is_dir():
        catalog.git_hooks = {
            path.name for path in hook_dir.iterdir()
            if path.is_file() and path.name != "README.md"
        }
    return catalog


def _resolve(reference: dict, catalog: Catalog) -> tuple[bool, str]:
    if not isinstance(reference, dict):
        return False, "covered_by entry is not an object"
    kind = reference.get("kind")
    item_id = reference.get("id")
    if kind not in VALID_KINDS or not isinstance(item_id, str) or not item_id:
        return False, "covered_by entry must have a valid kind and non-empty id"
    if kind == "metric":
        found = item_id in catalog.metrics
    elif kind == "verifier":
        path = PurePosixPath(item_id)
        safe = not path.is_absolute() and ".." not in path.parts and "\\" not in item_id
        if len(path.parts) == 1:
            verifier_id = path.name.removesuffix(".py")
        elif path.parts[:3] == ("Governance", "Order Samurai", "execution") and len(path.parts) == 4:
            verifier_id = path.name.removesuffix(".py")
        else:
            verifier_id = ""
        found = safe and verifier_id in catalog.verifiers
    elif kind == "doctor_family":
        found = item_id in catalog.doctor_families
    elif kind == "hook":
        found = item_id in catalog.hooks
    else:
        path = PurePosixPath(item_id)
        safe = not path.is_absolute() and ".." not in path.parts and "\\" not in item_id
        canonical = path.name if len(path.parts) == 1 else (
            path.name if path.parts == (".githooks", path.name) else ""
        )
        found = safe and canonical in catalog.git_hooks
    return found, "" if found else f"{kind} id does not resolve: {item_id}"


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _memory_scope(frontmatter: dict, now: datetime) -> tuple[bool, str | None]:
    metadata = frontmatter.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    note_type = frontmatter.get("type") or metadata.get("type")
    modified = _parse_time(frontmatter.get("modified") or metadata.get("modified"))
    if note_type not in {"feedback", "project"}:
        return False, None
    if modified is None:
        return False, "typed memory note has missing or unparseable modified timestamp"
    return now.astimezone(timezone.utc) - modified <= timedelta(days=90), None


def _finding(path: Path, frontmatter: dict, reason: str) -> dict:
    layer = frontmatter.get("layer")
    component = frontmatter.get("component") or frontmatter.get("module")
    group = str(layer or component or "unclassified")
    return {"path": str(path), "group": group, "reason": reason}


def _check_doc(path: Path, frontmatter: dict, catalog: Catalog) -> dict | None:
    coverage = frontmatter.get("covered_by")
    if isinstance(coverage, list) and coverage:
        failures = [reason for ok, reason in (_resolve(ref, catalog) for ref in coverage) if not ok]
        if not failures:
            return None
        return _finding(path, frontmatter, "; ".join(failures))

    disposition = frontmatter.get("disposition")
    if disposition == "out-of-scope" and frontmatter.get("layer"):
        return None
    if disposition == "accepted-workaround" and frontmatter.get("cost"):
        return None
    if disposition == "pending":
        return _finding(path, frontmatter, "pending is not coverage")
    if disposition:
        need = "layer" if disposition == "out-of-scope" else "cost"
        return _finding(path, frontmatter, f"invalid disposition {disposition!r}; missing {need}")
    return _finding(path, frontmatter, "no covered_by or valid disposition")


def analyze(
    *, solution_roots: tuple[Path, ...] = SOLUTION_ROOTS,
    memory_root: Path = MEMORY_ROOT, catalog: Catalog | None = None,
    repo_root: Path = REPO_ROOT, now: datetime | None = None,
    include_memory: bool = True,
) -> dict:
    now = now or datetime.now(timezone.utc)
    catalog = catalog or load_catalog(repo_root)
    docs: list[tuple[Path, dict]] = []
    unmeasured = list(catalog.errors)
    for root in solution_roots:
        if not root.is_dir():
            unmeasured.append(f"solution tree absent: {root}")
            continue
        docs.extend((path, parse_frontmatter(path)) for path in sorted(root.rglob("*.md")))
    if include_memory and not memory_root.is_dir():
        unmeasured.append(f"memory directory absent: {memory_root}")
    elif include_memory:
        for path in sorted(memory_root.glob("*.md")):
            frontmatter = parse_frontmatter(path)
            in_scope, reason = _memory_scope(frontmatter, now)
            if reason:
                unmeasured.append(f"{path}: {reason}")
            elif in_scope:
                docs.append((path, frontmatter))

    uncovered = [
        finding for path, frontmatter in docs
        if (finding := _check_doc(path, frontmatter, catalog)) is not None
    ]
    groups = Counter(item["group"] for item in uncovered)
    return {
        "scanned": len(docs),
        "covered": len(docs) - len(uncovered),
        "uncovered": uncovered,
        "groups": dict(groups.most_common()),
        "unmeasured": unmeasured,
    }


def run_checks(**kwargs) -> list[dict]:
    report = analyze(**kwargs)
    rows = [{
        "status": "WARN",
        "label": "incident-coverage.unmeasured",
        "detail": detail,
    } for detail in report["unmeasured"]]
    rows.extend({
        "status": "WARN",
        "label": "incident-coverage.uncovered",
        "detail": f"{item['group']}: {item['path']} ({item['reason']})",
    } for item in report["uncovered"])
    if not rows:
        rows.append({
            "status": "OK",
            "label": "incident-coverage.covered",
            "detail": f"all {report['scanned']} in-scope document(s) have coverage",
        })
    return rows


def run_doctor_checks(**kwargs) -> list[dict]:
    kwargs.setdefault("include_memory", False)
    report = analyze(**kwargs)
    rows = [{
        "status": "WARN",
        "label": "incident-coverage.unmeasured",
        "detail": detail,
    } for detail in report["unmeasured"]]
    rows.extend({
        "status": "WARN",
        "label": "incident-coverage.uncovered-family",
        "detail": f"{group}: {count} uncovered document(s)",
    } for group, count in list(report["groups"].items())[:5])
    if not rows:
        rows.append({
            "status": "OK",
            "label": "incident-coverage.covered",
            "detail": f"all {report['scanned']} in-scope document(s) have coverage",
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the full JSON report")
    parser.add_argument("--strict", action="store_true", help="exit 1 on uncovered or unmeasured input")
    args = parser.parse_args(argv)
    report = analyze()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for detail in report["unmeasured"]:
            print(f"[WARN] incident-coverage.unmeasured: {detail}")
        for item in report["uncovered"]:
            print(f"[WARN] incident-coverage.uncovered: {item['group']}: {item['path']} ({item['reason']})")
        print(
            f"incident coverage: {report['covered']}/{report['scanned']} covered; "
            f"{len(report['uncovered'])} uncovered, {len(report['unmeasured'])} unmeasured"
        )
    has_gaps = bool(report["uncovered"] or report["unmeasured"])
    return 1 if args.strict and has_gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
