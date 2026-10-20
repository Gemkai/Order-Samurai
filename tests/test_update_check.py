"""Update-available notice: free and Pro installs both learn when a newer build ships.

Buyers only get code from the zip, so a merged fix reaches them only when they rerun the
installer. `samurai doctor` and the dashboard payload compare the installed version with
`version` in the published dist/build-record.json. The check runs at most once a day
(cached under ~/.samurai), uses a short timeout, and is silent when offline: it never
changes doctor's result.
"""
from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentica_core import update_check  # noqa: E402

UPDATE_COMMAND = "curl -fsSL https://www.ordersamurai.ai/install.sh | bash"
FEEDBACK_LINE = "Feedback or problems: support@agentica-llc.biz"


@pytest.mark.parametrize("latest,current,expected", [
    ("2.2.2", "2.2.1", True),
    ("2.10.0", "2.9.9", True),
    ("3.0", "2.9.9", True),
    ("2.2.1", "2.2.1", False),
    ("2.2.0", "2.2.1", False),
    ("2.2.1", "2.2.1.0", False),
    ("garbage", "2.2.1", False),
    (None, "2.2.1", False),
    ("2.2.2", None, False),
])
def test_is_newer_compares_numerically(latest, current, expected):
    assert update_check.is_newer(latest, current) is expected


def test_installed_version_reads_the_cli_constant():
    text = (ROOT / "bin" / "samurai").read_text(encoding="utf-8")
    assert f'SAMURAI_VERSION = "{update_check.installed_version(ROOT)}"' in text


class _Fetch:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def __call__(self, url, timeout):
        self.calls += 1
        self.url, self.timeout = url, timeout
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_latest_version_is_fetched_at_most_once_a_day(tmp_path):
    fetch = _Fetch({"version": "2.3.0"})
    first = update_check.latest_version(home=tmp_path, now=1_000_000.0, fetch=fetch)
    again = update_check.latest_version(home=tmp_path, now=1_000_000.0 + 23 * 3600, fetch=fetch)
    assert (first, again, fetch.calls) == ("2.3.0", "2.3.0", 1)
    assert fetch.url.endswith("/dist/build-record.json")
    assert fetch.timeout <= 5
    fetch.result = {"version": "2.4.0"}
    later = update_check.latest_version(home=tmp_path, now=1_000_000.0 + 25 * 3600, fetch=fetch)
    assert (later, fetch.calls) == ("2.4.0", 2)


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("offline"), TimeoutError("slow"), OSError("no route"), ValueError("bad json"),
])
def test_offline_check_is_silent_and_keeps_the_last_known_version(tmp_path, failure):
    assert update_check.latest_version(home=tmp_path, now=0.0, fetch=_Fetch(failure)) is None
    update_check.latest_version(home=tmp_path, now=10 * 86400.0, fetch=_Fetch({"version": "2.3.0"}))
    fetch = _Fetch(failure)
    assert update_check.latest_version(home=tmp_path, now=20 * 86400.0, fetch=fetch) == "2.3.0"
    assert fetch.calls == 1


def test_a_failed_check_also_waits_a_day(tmp_path):
    fetch = _Fetch(urllib.error.URLError("offline"))
    update_check.latest_version(home=tmp_path, now=0.0, fetch=fetch)
    update_check.latest_version(home=tmp_path, now=3600.0, fetch=fetch)
    assert fetch.calls == 1


def test_corrupt_cache_is_ignored(tmp_path):
    (tmp_path / update_check.CACHE_NAME).write_text("{not json", encoding="utf-8")
    assert update_check.latest_version(home=tmp_path, now=0.0, fetch=_Fetch({"version": "2.3.0"})) == "2.3.0"


def test_opt_out_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_NO_UPDATE_CHECK", "1")
    fetch = _Fetch({"version": "9.9.9"})
    assert update_check.latest_version(home=tmp_path, now=0.0, fetch=fetch) is None
    assert fetch.calls == 0


def test_status_fields_for_the_dashboard_payload(tmp_path):
    fields = update_check.status("2.2.1", home=tmp_path, now=0.0, fetch=_Fetch({"version": "2.2.2"}))
    assert fields == {"current_version": "2.2.1", "latest_version": "2.2.2", "update_available": True}
    assert update_check.UPDATE_COMMAND == UPDATE_COMMAND
    offline = update_check.status("2.2.1", home=tmp_path / "x", now=0.0, fetch=_Fetch(OSError("down")))
    assert offline["latest_version"] is None and offline["update_available"] is False


def test_refresh_dashboard_reports_the_installed_version(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMURAI_HOME", str(tmp_path))
    monkeypatch.setenv("SAMURAI_NO_UPDATE_CHECK", "1")
    import refresh_dashboard  # noqa: PLC0415
    fields = refresh_dashboard._version_fields()
    assert fields["current_version"] == update_check.installed_version(ROOT)
    assert fields["update_available"] is False


# --- samurai doctor ------------------------------------------------------------------

def _serve_build_record(version: str):
    body = json.dumps({"version": version}).encode()
    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            hits.append(self.path)
            if not self.path.endswith("/build-record.json"):
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
    return server, hits


def _doctor(tmp_path: Path, base_url: str, **extra) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    root = tmp_path / "core"
    if not root.exists():
        shutil.copytree(ROOT / "bin", root / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "agentica_core", root / "agentica_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        (root / "state").mkdir()
        shutil.copy2(ROOT / "state/kill_chain_taxonomy.json", root / "state/kill_chain_taxonomy.json")
    path = tmp_path / "path"
    path.mkdir(exist_ok=True)
    if not (path / "python3").exists():
        (path / "python3").symlink_to(sys.executable)
    env = {
        "HOME": str(home), "PATH": str(path), "CODEX_HOME": str(home / ".codex"),
        "SAMURAI_CODEX_APP_BIN": str(tmp_path / "absent-app/codex"),
        "SAMURAI_APPLICATIONS_DIR": str(tmp_path / "Applications"),
        "SAMURAI_HOME": str(home / ".samurai"), "SAMURAI_ROOT": str(root),
        "SAMURAI_HARNESS": "claude", "SAMURAI_NO_PROMPT": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "OS_CORE_BASE_URL": base_url, **extra,
    }
    return subprocess.run([sys.executable, str(root / "bin/samurai"), "doctor"], env=env, cwd=home,
                          capture_output=True, text=True, timeout=60)


def test_doctor_shows_the_update_notice_and_keeps_its_exit_code(tmp_path):
    server, hits = _serve_build_record("99.0.0")
    try:
        base = f"http://127.0.0.1:{server.server_port}/dist"
        result = _doctor(tmp_path, base)
        again = _doctor(tmp_path, base)
    finally:
        server.shutdown()
    out = result.stdout
    assert result.returncode == 1, out + result.stderr  # nothing installed: unchanged failure
    assert "Update available: v99.0.0" in out, out
    assert UPDATE_COMMAND in out, out
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines[-1] == FEEDBACK_LINE and lines[-2].startswith("Summary: "), out
    assert "Update available: v99.0.0" in again.stdout
    assert hits == ["/dist/build-record.json"], "second doctor run must use the daily cache"


def test_doctor_is_silent_and_unchanged_when_offline(tmp_path):
    server, _ = _serve_build_record("99.0.0")
    port = server.server_port
    server.shutdown()
    server.server_close()  # nothing listens on this port now
    result = _doctor(tmp_path, f"http://127.0.0.1:{port}/dist")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Update available" not in result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert [line for line in result.stdout.splitlines() if line.strip()][-1] == FEEDBACK_LINE


def test_doctor_shows_nothing_when_up_to_date(tmp_path):
    server, _ = _serve_build_record(update_check.installed_version(ROOT))
    try:
        result = _doctor(tmp_path, f"http://127.0.0.1:{server.server_port}/dist")
    finally:
        server.shutdown()
    assert "Update available" not in result.stdout


def test_a_stalled_server_cannot_hold_the_check_past_its_deadline(tmp_path, monkeypatch):
    """urlopen's timeout is per socket read: a server dribbling one byte at a time would
    otherwise hold doctor (and install.sh's closing doctor run) for hours."""
    import time  # noqa: PLC0415

    class Dribble(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            for _ in range(1000):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.5)

        def log_message(self, format, *args):  # noqa: A002 -- http.server API
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Dribble)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OS_CORE_BASE_URL", f"http://127.0.0.1:{server.server_port}/dist")
    start = time.monotonic()
    try:
        assert update_check.latest_version(home=tmp_path, now=0.0) is None
    finally:
        server.shutdown()
    assert time.monotonic() - start < update_check.TIMEOUT_S + 1.5


def test_redirects_are_refused(tmp_path, monkeypatch):
    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            if self.path.startswith("/elsewhere"):
                body = json.dumps({"version": "99.0.0"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header("Location", "/elsewhere/build-record.json")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002 -- http.server API
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("OS_CORE_BASE_URL", f"http://127.0.0.1:{server.server_port}/dist")
    try:
        assert update_check.latest_version(home=tmp_path, now=0.0) is None
    finally:
        server.shutdown()


@pytest.mark.parametrize("bad", ["٣.٤", "1" * 5000, "1." * 40 + "1"])
def test_odd_or_oversized_versions_are_rejected_even_from_the_cache(tmp_path, bad):
    assert update_check.is_newer(bad, "2.2.1") is False
    (tmp_path / update_check.CACHE_NAME).write_text(
        json.dumps({"checked_at": 0.0, "latest_version": bad}), encoding="utf-8")
    assert update_check.latest_version(home=tmp_path, now=1.0, fetch=_Fetch({"version": "2.3.0"})) is None


def test_version_fields_pass_the_payload_schema(tmp_path):
    import jsonschema  # noqa: PLC0415
    from agentica_core.aggregate import validate_payload  # noqa: PLC0415
    base = {"schema_version": "agentica.1", "timestamp": "2026-10-20T00:00:00Z", "reflexes": []}
    for fetched in ({"version": "2.2.2"}, OSError("offline")):
        version = update_check.status("2.2.1", home=tmp_path / str(id(fetched)), now=0.0, fetch=_Fetch(fetched))
        validate_payload({**base, "version": version})
    with pytest.raises(jsonschema.ValidationError):
        validate_payload({**base, "version": {"current_version": "2.2.1"}})
