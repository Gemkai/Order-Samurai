"""The web installer must survive a CDN pairing a stale zip with a fresh .sha256.

raw.githubusercontent.com caches the zip and its .sha256 separately, so right after a
release one can be old and the other new (seen on the website CDN 2026-10-07). The
installer re-downloads both, bypassing the cache, and still refuses a zip that never
matches its published checksum.
"""
from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ZIP = "order-samurai-core.zip"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("bash", "curl", "unzip", "shasum")),
    reason="web installer needs bash, curl, unzip and shasum",
)


def _serve(stale_zip_responses: int):
    """Serve dist/'s zip and sidecar; the first N zip requests get stale bytes."""
    real_zip = (ROOT / "dist" / ZIP).read_bytes()
    sidecar = (ROOT / "dist" / f"{ZIP}.sha256").read_bytes()
    state = {"zip_requests": 0, "paths": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            state["paths"].append(self.path)
            name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
            if name == ZIP:
                state["zip_requests"] += 1
                body = b"stale archive" if state["zip_requests"] <= stale_zip_responses else real_zip
            elif name == f"{ZIP}.sha256":
                body = sidecar
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002 -- http.server API
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state


def _run_web_installer(tmp_path: Path, base_url: str) -> subprocess.CompletedProcess:
    # A copy outside the checkout has no bin/samurai beside it, so it takes the web path.
    script = tmp_path / "install.sh"
    shutil.copy2(ROOT / "install.sh", script)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), OS_CORE_BASE_URL=base_url, OS_SHA_RETRY_DELAY="0",
               SAMURAI_NO_PROMPT="1")
    for key in ("SAMURAI_HOME", "SAMURAI_ROOT", "ORDER_SAMURAI_ROOT", "SAMURAI_LICENSE_KEY", "PYTHONPATH"):
        env.pop(key, None)
    return subprocess.run(["bash", str(script)], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=300)


def test_web_install_recovers_when_the_first_download_is_stale(tmp_path):
    server, state = _serve(stale_zip_responses=1)
    try:
        r = _run_web_installer(tmp_path, f"http://127.0.0.1:{server.server_port}/dist")
    finally:
        server.shutdown()
    out = r.stdout + r.stderr
    assert state["zip_requests"] == 2, (state, out[-2000:])
    assert any("?" in p for p in state["paths"][2:]), state["paths"]
    assert (tmp_path / "home" / ".samurai" / "core" / "bin" / "samurai").is_file(), out[-2000:]
    assert "CHECKSUM MISMATCH" not in out, out[-2000:]


def test_web_install_still_refuses_a_zip_that_never_matches(tmp_path):
    server, state = _serve(stale_zip_responses=10)
    try:
        r = _run_web_installer(tmp_path, f"http://127.0.0.1:{server.server_port}/dist")
    finally:
        server.shutdown()
    assert r.returncode != 0, r.stdout[-2000:]
    assert "CHECKSUM MISMATCH" in r.stderr, r.stderr[-2000:]
    assert state["zip_requests"] == 3, state
    assert not (tmp_path / "home" / ".samurai" / "core").exists(), "extracted a mismatched archive"


def test_dashboard_copy_of_the_installer_matches():
    assert (ROOT / "dashboard-ui" / "public" / "install.sh").read_bytes() == (ROOT / "install.sh").read_bytes()
