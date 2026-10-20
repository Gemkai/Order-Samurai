"""install.sh offers the full audit after doctor passes, asking on /dev/tty only.

Under `curl | bash` stdin IS the script: a prompt that read stdin would swallow the rest
of the installer. So the question goes to /dev/tty, is skipped without a terminal, and
nothing reads stdin. bin/samurai is a stub that records each subcommand it is run with.
"""
from __future__ import annotations

import hashlib
import os
import pty
import select
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROMPT = "Run a full audit now? Results appear in the Reports tab. [Y/n]"
SCRIPTS = ["install.sh", "dashboard-ui/public/install.sh"]

STUB = """import os, sys
from pathlib import Path
if "--help" in sys.argv:  # capability probe: an older CLI has no `audit`
    sys.exit(2 if os.environ.get("NO_AUDIT") == "1" else 0)
with Path(os.environ["MARKER"]).open("a") as out:
    out.write(sys.argv[1] + "\\n")
sys.exit(1 if sys.argv[1] == os.environ.get("FAIL_CMD") else 0)
"""


def _env(tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
           "MARKER": str(tmp_path / "marker.txt"), "TMPDIR": str(tmp_path),
           "OS_SHA_RETRY_DELAY": "0", "TERM": "dumb"}
    env.update(extra)
    return env


def _local_pkg(tmp_path, script_name):
    pkg = tmp_path / "pkg"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "install.sh").write_text((ROOT / script_name).read_text(encoding="utf-8"), encoding="utf-8")
    (pkg / "bin" / "samurai").write_text(STUB, encoding="utf-8")
    return pkg


def _calls(tmp_path):
    marker = tmp_path / "marker.txt"
    return marker.read_text(encoding="utf-8").split() if marker.exists() else []


def _run_on_pty(argv, env, answer=None, stdin_bytes=None, timeout=30):
    """Run argv with a pty as its controlling terminal (and stdout). With stdin_bytes,
    stdin is a pipe carrying those bytes instead -- the `curl | bash` shape."""
    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            if stdin_bytes is not None:
                r, w = os.pipe()
                os.write(w, stdin_bytes)
                os.close(w)
                os.dup2(r, 0)
            os.execve(argv[0], argv, env)
        finally:
            os._exit(127)
    out, sent, deadline = b"", False, time.monotonic() + timeout
    while True:
        if time.monotonic() > deadline:
            os.kill(pid, 9)
            pytest.fail("installer hung: " + out.decode(errors="replace"))
        if not select.select([fd], [], [], 0.2)[0]:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # EIO: the child side of the pty closed
            break
        if not chunk:
            break
        out += chunk
        if answer is not None and not sent and PROMPT.encode() in out:
            os.write(fd, answer.encode())
            sent = True
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


@pytest.mark.parametrize("script_name", SCRIPTS)
@pytest.mark.parametrize("answer", ["\n", "y\n", "Y\n", "yes\n"])
def test_tty_yes_runs_the_audit_after_doctor(tmp_path, script_name, answer):
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")], _env(tmp_path), answer=answer)
    assert code == 0, out
    assert PROMPT in out
    assert _calls(tmp_path) == ["install", "doctor", "audit"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_tty_no_skips_the_audit(tmp_path, script_name):
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")], _env(tmp_path), answer="n\n")
    assert code == 0, out
    assert _calls(tmp_path) == ["install", "doctor"]
    assert f'Run it any time: python3 "{pkg / "bin" / "samurai"}" audit' in out


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_no_tty_skips_the_question_and_never_reads_stdin(tmp_path, script_name):
    pkg = _local_pkg(tmp_path, script_name)
    result = subprocess.run(["/bin/bash", str(pkg / "install.sh")], env=_env(tmp_path),
                            input="y\n", capture_output=True, text=True, timeout=30,
                            start_new_session=True)  # no controlling terminal: /dev/tty fails
    assert result.returncode == 0, result.stdout + result.stderr
    assert PROMPT not in result.stdout + result.stderr
    assert _calls(tmp_path) == ["install", "doctor"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_no_prompt_env_skips_the_question(tmp_path, script_name):
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")],
                            _env(tmp_path, SAMURAI_NO_PROMPT="1"), answer="y\n", timeout=15)
    assert code == 0, out
    assert PROMPT not in out
    assert _calls(tmp_path) == ["install", "doctor"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_failed_doctor_asks_nothing(tmp_path, script_name):
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")],
                            _env(tmp_path, FAIL_CMD="doctor"), answer="y\n", timeout=15)
    assert code != 0, out
    assert PROMPT not in out
    assert _calls(tmp_path) == ["install", "doctor"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_audit_findings_do_not_fail_the_install(tmp_path, script_name):
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")],
                            _env(tmp_path, FAIL_CMD="audit"), answer="\n")
    assert code == 0, out
    assert _calls(tmp_path) == ["install", "doctor", "audit"]


def _published_zip(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    archive = dist / "order-samurai-core.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/samurai", STUB)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (dist / "order-samurai-core.zip.sha256").write_text(f"{digest}  order-samurai-core.zip\n", encoding="utf-8")
    return dist


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_curl_pipe_bash_asks_on_the_tty_and_finishes_the_script(tmp_path, script_name):
    """stdin is the script itself. A prompt that read stdin would eat the script's
    remaining lines (and never see the person's answer)."""
    dist = _published_zip(tmp_path)
    script = (ROOT / script_name).read_bytes() + b'\necho "SCRIPT-RAN-TO-END"\n'
    env = _env(tmp_path, OS_CORE_BASE_URL=f"file://{dist}")
    code, out = _run_on_pty(["/bin/bash"], env, answer="\n", stdin_bytes=script)
    assert code == 0, out
    assert "Web Installer" in out and PROMPT in out
    assert "SCRIPT-RAN-TO-END" in out
    assert _calls(tmp_path) == ["install", "doctor", "audit"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_cli_without_audit_is_not_offered_one(tmp_path, script_name):
    """main's installer can be newer than the zip it downloads (v2.2.1 has no `audit`)."""
    pkg = _local_pkg(tmp_path, script_name)
    code, out = _run_on_pty(["/bin/bash", str(pkg / "install.sh")],
                            _env(tmp_path, NO_AUDIT="1"), answer="y\n", timeout=15)
    assert code == 0, out
    assert PROMPT not in out
    assert _calls(tmp_path) == ["install", "doctor"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_redirected_stdout_skips_the_question(tmp_path, script_name):
    """`bash install.sh >log` from a terminal (the release check does this) must not
    block on a question nobody sees -- the same rule as the license-key prompt."""
    pkg = _local_pkg(tmp_path, script_name)
    log = tmp_path / "out.txt"
    code, out = _run_on_pty(["/bin/bash", "-c", f'/bin/bash "{pkg / "install.sh"}" >"{log}" 2>&1'],
                            _env(tmp_path), answer="y\n", timeout=15)
    assert code == 0, out + log.read_text()
    assert PROMPT not in out
    assert _calls(tmp_path) == ["install", "doctor"]


LEGACY = ["2026-W28__antigravity.md", "2026-W28__claude.md", "2026-W28__codex.md",
          "2026-W29__claude.md", "2026-W29__codex.md", "current-state.md"]


@pytest.mark.parametrize("script_name", SCRIPTS)
def test_upgrade_removes_the_sample_reports_v2_2_1_shipped(tmp_path, script_name):
    """v2.2.1's zip put the vendor's July reports in public/reports; unzip -o keeps them,
    and they would show as the buyer's own history. The buyer's audits stay."""
    reports = tmp_path / "home" / ".samurai" / "core" / "dashboard-ui" / "public" / "reports"
    reports.mkdir(parents=True)
    for name in LEGACY:
        (reports / name).write_text("vendor sample", encoding="utf-8")
    (reports / "index.json").write_text('[{"file": "2026-W29__claude.md", "week": "2026-W29"}]', encoding="utf-8")
    (reports / "audit__20261019T120000Z.md").write_text("# Full Audit", encoding="utf-8")
    dist = _published_zip(tmp_path)
    r = subprocess.run(["/bin/bash"], input=(ROOT / script_name).read_text(encoding="utf-8"),
                       env=_env(tmp_path, OS_CORE_BASE_URL=f"file://{dist}"), capture_output=True,
                       text=True, timeout=60, start_new_session=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(p.name for p in reports.iterdir()) == ["audit__20261019T120000Z.md"]
