"""Generate the discoverability manifest from approved on-disk surfaces.

Emits config/hub_capability_manifest.json — a path-identified, deterministically
ordered list of the repo's discoverable live/support surfaces, generated from
disk. Archive, exploratory, dependency, state, and metadata roots are excluded
(a manifest must only advertise surfaces that are real and runtime-approved).

Consumed by execution/verify_registry_truth.py, which resolves every surfaces[].path
against disk; because this manifest is generated from disk, every entry resolves.

Run: python3 execution/sync_capability_manifest.py [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.runtime_paths import (  # noqa: E402
    CONFIG_DIR,
    REPO_ROOT,
    ROOT_HYGIENE_POLICY_PATH,
)

sys.stdout.reconfigure(encoding="utf-8")  # anti-pattern #13 guard

OUTPUT_PATH = CONFIG_DIR / "hub_capability_manifest.json"

# Hygiene classifications that are NOT discoverable capability surfaces.
NON_DISCOVERABLE = {"archive", "dependency", "state", "metadata"}
# Map hygiene classification -> anti_sprawl_policy surfaceRole vocabulary.
ROLE_BY_CLASSIFICATION = {"live": "runtime", "support": "support"}
# Per-surface role overrides where a more specific role than its class applies.
ROLE_OVERRIDES = {"config": "registry", "bin": "operator"}


def build_manifest(repo_root: Path = REPO_ROOT) -> dict:
    policy = json.loads(ROOT_HYGIENE_POLICY_PATH.read_text(encoding="utf-8"))
    directories = policy.get("directories", {})

    surfaces = []
    for classification, names in directories.items():
        if classification in NON_DISCOVERABLE:
            continue
        role = ROLE_BY_CLASSIFICATION.get(classification, "support")
        for name in names:
            if not (repo_root / name).is_dir():
                continue  # only advertise surfaces that actually exist on disk
            surfaces.append(
                {
                    "path": name,
                    "role": ROLE_OVERRIDES.get(name, role),
                    "discoverable": True,
                }
            )

    surfaces.sort(key=lambda s: s["path"])  # deterministic, path-based identity
    return {
        "generator": "execution/sync_capability_manifest.py",
        "surfaceCount": len(surfaces),
        "surfaces": surfaces,
    }


def main() -> int:
    manifest = build_manifest()
    if "--json" in sys.argv[1:]:
        print(json.dumps(manifest, indent=2))
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    paths = ", ".join(s["path"] for s in manifest["surfaces"])
    print(f"wrote {manifest['surfaceCount']} surfaces -> {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  surfaces: {paths}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
