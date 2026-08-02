"""harness_config — the read side of the declared editable surface.

`Order Samurai/harness/editable_surface.json` declares every harness knob a self-harness
proposer is allowed to touch (M1 of Research/SELF_HARNESS_EVOLUTION_PLAN.md). This module is
the ONLY sanctioned way to read it.

Two invariants matter more than convenience:

1. **Behaviour neutrality.** Every value in the surface file equals the live value its consumer
   already used, so routing a consumer through `get_value` changes nothing observable. If you
   are adding a knob, copy the live constant -- never "improve" it in the same change.
2. **Env override wins.** `OS_HARNESS_<KEY_UPPER>` beats the file. This preserves the existing
   env-driven operator escape hatch and lets a validation worktree pin a candidate value without
   editing the file it is measuring.

Out-of-range values raise rather than clamp: a surface file that disagrees with its own bounds is
a broken contract, and silently clamping would let a bad proposal look accepted.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any, Optional

_THIS = Path(__file__).resolve()
_PRODUCT_ROOT = _THIS.parents[1]
_SURFACE = (_PRODUCT_ROOT / "harness" / "editable_surface.json") if (_PRODUCT_ROOT / "harness" / "editable_surface.json").exists() else (_PRODUCT_ROOT / "Order Samurai" / "harness" / "editable_surface.json")

_ENV_PREFIX = "OS_HARNESS_"


def surface_path() -> Path:
    """Canonical location of the editable-surface declaration."""
    return _SURFACE


def _coerce(raw: Any, type_name: str, key: str) -> Any:
    if type_name == "int":
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"harness surface key {key!r}: {raw!r} is not an int") from exc
    if type_name == "text":
        return str(raw)
    raise ValueError(f"harness surface key {key!r}: unsupported type {type_name!r}")


def _check_bounds(key: str, value: Any, spec: dict) -> None:
    if spec.get("type") == "int":
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and value < lo:
            raise ValueError(f"harness surface key {key!r}: {value} < min {lo}")
        if hi is not None and value > hi:
            raise ValueError(f"harness surface key {key!r}: {value} > max {hi}")
    elif spec.get("type") == "text":
        cap = spec.get("max_chars")
        if cap is not None and len(value) > cap:
            raise ValueError(f"harness surface key {key!r}: {len(value)} chars > max_chars {cap}")
        # A text knob has to survive the round trip through surface.env, which is one line per
        # key read by two runtimes. Newlines truncate it; a single quote breaks the shell quoting
        # that lets the value contain spaces at all. Both are rejected at the READ, not just at
        # the proposal gate, so a hand-edited surface cannot smuggle in a value the env file
        # would silently render differently for TS than for Python.
        if "\n" in value:
            raise ValueError(f"harness surface key {key!r}: text values must be a single line")
        if "'" in value:
            raise ValueError(f"harness surface key {key!r}: text values may not contain \"'\"")


def load_surface(path: Optional[Path] = None) -> dict:
    """Parse + validate the surface file. Raises ValueError on any out-of-range declared value."""
    p = path or _SURFACE
    data = json.loads(p.read_text(encoding="utf-8"))
    values = data.get("values")
    if not isinstance(values, dict):
        raise ValueError(f"harness surface {p}: missing or malformed `values` block")
    for key, spec in values.items():
        if not isinstance(spec, dict) or "value" not in spec or "type" not in spec:
            raise ValueError(f"harness surface key {key!r}: spec must carry `value` and `type`")
        _check_bounds(key, _coerce(spec["value"], spec["type"], key), spec)
    return data


def _resolve(key: str, spec: dict) -> Any:
    """Env override (OS_HARNESS_<KEY>) first, then the declared file value; coerced and
    bounds-checked. The single home of the override-resolution rule so get_value and the
    effective fingerprint cannot drift apart."""
    env_raw = os.environ.get(_ENV_PREFIX + key.upper())
    raw = env_raw if env_raw is not None else spec["value"]
    value = _coerce(raw, spec["type"], key)
    _check_bounds(key, value, spec)
    return value


def get_value(key: str, path: Optional[Path] = None) -> Any:
    """Resolve one knob: env override (OS_HARNESS_<KEY>) first, then the declared file value.

    An env override is bounds-checked exactly like a file value -- the escape hatch may not
    smuggle a value the surface says is illegal.
    """
    data = load_surface(path)
    values = data["values"]
    if key not in values:
        raise KeyError(f"{key!r} is not a declared harness surface key")
    return _resolve(key, values[key])


def surface_fingerprint(path: Optional[Path] = None) -> str:
    """Stable 12-hex digest of the surface's declared values.

    Canonicalised (sorted keys, no whitespace) over the `values` block only, so reformatting the
    file or editing a `note` does not invalidate the fingerprint of an unchanged harness. This is
    the ATDP execution-envelope field stamped onto every trace: it answers "which harness version
    produced this run".
    """
    data = load_surface(path)
    canon = json.dumps(data["values"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def effective_surface_fingerprint(path: Optional[Path] = None) -> str:
    """Fingerprint of the surface as ACTUALLY IN EFFECT: each declared value replaced by its
    OS_HARNESS_<KEY> override when one is set, then hashed exactly like surface_fingerprint.

    With no overrides this is byte-identical to surface_fingerprint (baseline runs unchanged).
    A pinned candidate run (self_harness_cycle applies its edits via the env channel) gets a
    fingerprint reflecting the surface it ran with, not the baseline file -- so a candidate's
    trace answers "which harness version produced this run" truthfully.
    """
    data = load_surface(path)
    effective = {k: {**spec, "value": _resolve(k, spec)} for k, spec in data["values"].items()}
    canon = json.dumps(effective, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def as_env_lines(path: Optional[Path] = None) -> list[str]:
    """Render declared values as `OS_HARNESS_<KEY>=<value>` lines for non-Python consumers.

    The TS engine and shell-sourced env files cannot import this module; `bin/render_surface_env.py`
    writes these lines to `harness/surface.env` so both runtimes read one declaration.

    Text values are wrapped in single quotes so a clause containing spaces survives a sourcing
    shell (`VAR=a b` runs `b`). The wrapping is trivially reversible because `_check_bounds`
    already refuses a text value containing a single quote — the reader strips one leading and
    one trailing `'`, and there is no escaping dialect for the two runtimes to disagree about.
    """
    data = load_surface(path)
    out = []
    for key, spec in sorted(data["values"].items()):
        raw = spec["value"]
        rendered = f"'{raw}'" if spec.get("type") == "text" else raw
        out.append(f"{_ENV_PREFIX}{key.upper()}={rendered}")
    return out
