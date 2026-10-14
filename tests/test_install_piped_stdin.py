"""A stdin installer must not trust bin/samurai in the caller's directory."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    # Bash reads this before the installer. Mock curl even for the loopback URL:
    # exercise real branch selection without opening any network connection.
    bash_env = tmp_path / "mock-network.sh"
    bash_env.write_text("curl() { return 7; }\n", encoding="utf-8")
    env = {
        "HOME": str(home), "SAMURAI_HOME": str(home / ".samurai"),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "OS_CORE_BASE_URL": "http://127.0.0.1:9", "OS_SHA_RETRY_DELAY": "0",
        "BASH_ENV": str(bash_env), "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    monkeypatch.setattr(os, "environ", env.copy())
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return env


def _bash(kind, tmp_path, env):
    if kind == "system":
        if not Path("/bin/bash").is_file():
            pytest.skip("/bin/bash is absent")
        return "/bin/bash"
    candidates = ["/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")]
    for candidate in dict.fromkeys(candidates):
        if not candidate or not Path(candidate).is_file():
            continue
        version = subprocess.run(
            [candidate, "--version"], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"version (\d+)\.", version.stdout)
        if version.returncode == 0 and match and int(match.group(1)) >= 4:
            return candidate
    pytest.skip("no modern bash (major >= 4) is installed")


def _marker_cli(checkout, marker):
    (checkout / "bin").mkdir(parents=True)
    (checkout / "bin" / "samurai").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a') as out:\n"
        "    out.write(sys.argv[1] + '\\n')\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("bash_kind", ["system", "modern"])
@pytest.mark.parametrize("stdin_mode", ["pipe", "redirect"])
@pytest.mark.parametrize("script_name", ["install.sh", "dashboard-ui/public/install.sh"])
def test_stdin_never_runs_cwd_cli(tmp_path, isolated, script_name, stdin_mode, bash_kind):
    bash = _bash(bash_kind, tmp_path, isolated)
    marker = tmp_path / "marker.txt"
    caller = tmp_path / "caller"
    _marker_cli(caller, marker)
    script = ROOT / script_name
    kwargs = dict(cwd=caller, env=isolated, capture_output=True, timeout=20)
    if stdin_mode == "pipe":
        result = subprocess.run([bash], input=script.read_bytes(), **kwargs)
    else:
        with script.open("rb") as stdin:
            result = subprocess.run([bash], stdin=stdin, **kwargs)
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert "needs Python 3.11" not in stderr, stderr
    assert not marker.exists() or marker.read_bytes() == b"", stdout
    assert "Local Installer" not in stdout, stdout


def test_explicit_checkout_installer_uses_its_own_cli(tmp_path, isolated):
    bash = _bash("system", tmp_path, isolated)
    checkout = tmp_path / "checkout"
    marker = tmp_path / "marker.txt"
    _marker_cli(checkout, marker)
    shutil.copyfile(ROOT / "install.sh", checkout / "install.sh")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [bash, str(checkout / "install.sh")], cwd=elsewhere, env=isolated,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Local Installer" in result.stdout
    assert marker.read_text() == "install\ndoctor\n"
