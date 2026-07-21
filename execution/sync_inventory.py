"""Generate the factual repo inventory from disk reality.

Answers the existence question "what top-level surfaces exist" by classifying
every root entry against config/root_hygiene_policy.json. Output is deterministic
(sorted, no wall-clock) so re-running produces no spurious diff, and is generated
from disk rather than hand-curated — satisfying the anti-drift
`generated-truth-over-manual-inventory` rule.

Writes artifacts/inventory.json. Run: python3 execution/sync_inventory.py [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import (  # noqa: E402
    ARTIFACTS_DIR,
    REPO_ROOT,
    ROOT_HYGIENE_POLICY_PATH,
)

sys.stdout.reconfigure(encoding="utf-8")  # anti-pattern #13 guard

OUTPUT_PATH = ARTIFACTS_DIR / "inventory.json"


def _classification_index(policy: dict) -> tuple[dict, dict]:
    """Reverse the hygiene policy into {name: classification} maps for dirs / files."""
    dir_class: dict[str, str] = {}
    for classification, names in policy.get("directories", {}).items():
        for name in names:
            dir_class[name] = classification
    file_class: dict[str, str] = {}
    for classification, names in policy.get("files", {}).items():
        for name in names:
            file_class[name] = classification
    return dir_class, file_class


def build_inventory(repo_root: Path = REPO_ROOT) -> dict:
    policy = json.loads(ROOT_HYGIENE_POLICY_PATH.read_text(encoding="utf-8"))
    dir_class, file_class = _classification_index(policy)

    entries = []
    for child in sorted(repo_root.iterdir(), key=lambda p: p.name):
        if child.is_dir():
            classification = dir_class.get(child.name, "unclassified")
            kind = "dir"
        else:
            classification = file_class.get(child.name, "unclassified")
            kind = "file"
        entries.append(
            {"path": child.name, "type": kind, "classification": classification}
        )

    return {
        "generator": "execution/sync_inventory.py",
        "repoRoot": ".",
        "entryCount": len(entries),
        "entries": entries,
    }


def main() -> int:
    inventory = build_inventory()
    if "--json" in sys.argv[1:]:
        print(json.dumps(inventory, indent=2))
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    unclassified = [e["path"] for e in inventory["entries"] if e["classification"] == "unclassified"]
    print(f"wrote {inventory['entryCount']} entries -> {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    if unclassified:
        print(f"  unclassified ({len(unclassified)}): {', '.join(unclassified)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
