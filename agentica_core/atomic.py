"""Atomic JSON write — write-to-tmp + Windows-safe replace.

Mirrors the helper used by Order Samurai's scouts (kill_chain_discovery_scout.py).
Used by aggregate.write_payload to prevent torn reads of wid_payload.json while
the TS reflex-engine is watching it (H1, governance opt-in grant hardening).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def atomic_json_write(path: Path, data: Any) -> None:
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
