#!/usr/bin/env python3
"""Validates the synthetic demo payload structure, bounds, and ratios."""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PATH = REPO_ROOT / "dashboard-ui" / "public" / "wid_payload.json"

def validate_payload(path: Path) -> list[str]:
    errors = []
    if not path.exists():
        return [f"Payload file does not exist: {path}"]
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"Failed to parse JSON: {e}"]

    # 1. Check pillar ratios: 0 <= passing <= graded
    pillars = data.get("pillars", {})
    for pk, pdata in pillars.items():
        if not isinstance(pdata, dict):
            continue
        rollup = pdata.get("rollup", {})
        if isinstance(rollup, dict):
            passing = rollup.get("passing")
            graded = rollup.get("graded")
            if passing is not None and graded is not None:
                if passing < 0 or graded < 0:
                    errors.append(f"Negative metric count in {pk} rollup: passing={passing}, graded={graded}")
                if passing > graded:
                    errors.append(f"Incoherent ratio in {pk} rollup: passing ({passing}) > graded ({graded})")

    # 2. Check no unmasked personal keys
    text = json.dumps(data)
    if "jemakaib" in text.lower() and "github.com/Gemkai" not in text:
        errors.append("Unmasked personal identifier found in payload")
    
    return errors

if __name__ == "__main__":
    errs = validate_payload(PAYLOAD_PATH)
    if errs:
        for err in errs:
            print(f"[FAIL] {err}", file=sys.stderr)
        sys.exit(1)
    print("[PASS] Synthetic demo payload is structurally valid.")
    sys.exit(0)
