"""Update-available check, the same for Free and Pro installs.

Compares the installed version (SAMURAI_VERSION in bin/samurai) with ``version`` in the
published dist/build-record.json, fetched from the same base URL install.sh downloads the
zip from. At most one fetch a day: the result (or the failure) is cached in
``~/.samurai/update_check.json``. Offline, slow or malformed responses are silent and keep
the last known version. Set SAMURAI_NO_UPDATE_CHECK=1 to never contact the network.

Stdlib only: ``samurai doctor`` imports it on a bare Python install.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/Gemkai/order-samurai/main/dist"  # install.sh
UPDATE_COMMAND = "curl -fsSL https://www.ordersamurai.ai/install.sh | bash"
CACHE_NAME = "update_check.json"
CHECK_INTERVAL_S = 24 * 3600
TIMEOUT_S = 3.0  # whole request, not per socket read
MAX_VERSION_LEN = 32

Fetch = Callable[[str, float], dict]


def _parse(version: str | None) -> tuple[int, ...] | None:
    if (not isinstance(version, str) or len(version) > MAX_VERSION_LEN
            or not re.fullmatch(r"[0-9]+(\.[0-9]+)*", version.strip())):
        return None
    parts = [int(p) for p in version.strip().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()  # 2.2.1 == 2.2.1.0
    return tuple(parts)


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = _parse(latest), _parse(current)
    return a is not None and b is not None and a > b


def installed_version(root: Path) -> str | None:
    """SAMURAI_VERSION from <root>/bin/samurai, the single version source."""
    try:
        text = (Path(root) / "bin" / "samurai").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^SAMURAI_VERSION = "([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _samurai_home() -> Path:
    return Path(os.environ.get("SAMURAI_HOME") or Path.home() / ".samurai")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # the published file never redirects
        return None


def _http_fetch(url: str, timeout: float) -> dict:
    """GET with a hard deadline. urlopen's timeout restarts on every socket read and does
    not cover DNS, so the request runs in a daemon thread that is abandoned at the deadline."""
    result: dict = {}

    def run() -> None:
        try:
            req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(req, timeout=timeout) as resp:  # fixed https base or operator override
                result["body"] = resp.read(64 * 1024)
        except Exception as e:  # noqa: BLE001 -- reported to the caller below
            result["error"] = e

    worker = threading.Thread(target=run, name="samurai-update-check", daemon=True)
    worker.start()
    worker.join(timeout)
    if "body" not in result:
        raise result.get("error") or TimeoutError(f"no response within {timeout}s")
    return json.loads(result["body"])


def latest_version(*, home: Path | None = None, now: float | None = None,
                   fetch: Fetch | None = None) -> str | None:
    """Newest published version, or None when unknown. Never raises."""
    if os.environ.get("SAMURAI_NO_UPDATE_CHECK", "").strip() not in ("", "0"):
        return None
    cache = Path(home or _samurai_home()) / CACHE_NAME
    now = time.time() if now is None else now
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            cached = {}
        known = cached.get("latest_version") if _parse(cached.get("latest_version")) else None
    except (OSError, ValueError):
        cached, known = {}, None
    checked_at = cached.get("checked_at")
    if isinstance(checked_at, (int, float)) and 0 <= now - checked_at < CHECK_INTERVAL_S:
        return known

    base = os.environ.get("OS_CORE_BASE_URL") or DEFAULT_BASE_URL
    try:
        record = (fetch or _http_fetch)(f"{base.rstrip('/')}/build-record.json", TIMEOUT_S)
        version = record.get("version") if isinstance(record, dict) else None
        if _parse(version):
            known = str(version).strip()
    except Exception:  # offline, timeout, HTTP error, bad JSON: keep the last known version
        pass
    try:  # atomic: doctor and a dashboard refresh may race
        cache.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache.parent, prefix=".update_check.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"checked_at": now, "latest_version": known}, f)
        os.replace(tmp, cache)
    except OSError:
        pass
    return known


def status(current: str | None, **kwargs) -> dict:
    """Fields for the dashboard payload and the doctor notice."""
    latest = latest_version(**kwargs)
    return {"current_version": current, "latest_version": latest,
            "update_available": is_newer(latest, current)}
