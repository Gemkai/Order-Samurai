"""bin/ronin-local must never turn "the model didn't answer" into a clean pass.

2026-08-16 audit, P2 (fail-open). The script ended with

    echo "$RESP" | jq -r '.choices[0].message.content // empty'

jq exits 0 on `// empty`, so an HTTP 200 carrying empty/absent content — the
documented behaviour of thinking builds on this host, and of the malformed-response
class that left the local tier silently dead for a month — produced empty stdout and
exit 0. prompts/ronin_sword.md routes secret scanning and CVE lookup through this
script, and its consumer is an LLM reading prose: empty output is indistinguishable
from "no secrets found".

These tests stand up a fake Ollama on loopback and point the script at it, so they
exercise the real bash/jq/curl path rather than a re-implementation.
"""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "ronin-local"

pytestmark = pytest.mark.skipif(
    not shutil.which("jq") or not shutil.which("curl"),
    reason="ronin-local is a bash script requiring jq + curl",
)


def _serve(payload, status=200):
    """A stub /v1/chat/completions returning `payload`. Yields its base URL."""
    body = json.dumps(payload).encode()

    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/v1"


def _run(payload, status=200):
    httpd, base = _serve(payload, status)
    try:
        return subprocess.run(
            [str(SCRIPT), "scan this file for secrets, keys, tokens"],
            capture_output=True, text=True, timeout=60,
            # RONIN_LOCAL_FALLBACK='' disables the cloud fallback so these tests
            # never shell out to a real `claude`.
            env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                 "OLLAMA_BASE": base, "RONIN_LOCAL_FALLBACK": ""},
            stdin=subprocess.DEVNULL,
        )
    finally:
        httpd.shutdown()


# ── the fail-open ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    {"content": ""},                      # thinking build: 200 with empty content
    {"content": None},                    # explicit null
    {},                                   # content key absent entirely
    {"content": "   \n\t  "},             # whitespace only
])
def test_empty_content_is_a_failure_not_a_clean_scan(message):
    r = _run({"choices": [{"message": message}]})
    assert r.returncode == 2, (
        "empty model output must exit non-zero; exit 0 with empty stdout reads as "
        f"'no secrets found' to the calling ronin (stdout={r.stdout!r})"
    )
    assert not r.stdout.strip(), "must not emit a fake clean result"
    assert "no content" in r.stderr.lower()


def test_2xx_error_body_is_a_failure():
    """Ollama can answer 200 with an error envelope and no choices at all."""
    r = _run({"error": {"message": "model 'gemma4:4b' not found"}})
    assert r.returncode == 2
    assert "not found" in r.stderr


# ── real answers still work ──────────────────────────────────────────────────

def test_normal_content_is_returned_and_exits_zero():
    r = _run({"choices": [{"message": {"content": "no secrets found in this file"}}]})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "no secrets found in this file"


@pytest.mark.parametrize("field", ["reasoning", "thinking"])
def test_thinking_builds_fall_back_to_the_reasoning_field(field):
    """Parity with agentica_core/llm/local_guards.py::extract_message_text —
    qwen-class builds put the answer there when content comes back empty."""
    r = _run({"choices": [{"message": {"content": "", field: "the reasoned answer"}}]})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "the reasoned answer"


def test_multiline_content_is_preserved():
    r = _run({"choices": [{"message": {"content": "line one\nline two"}}]})
    assert r.returncode == 0
    assert r.stdout.strip().splitlines() == ["line one", "line two"]
