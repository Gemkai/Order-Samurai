"""Installers without a script file must positively select the Web branch."""
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
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )
        match = re.search(r"version (\d+)\.", version.stdout)
        if version.returncode == 0 and match and int(match.group(1)) >= 4:
            return candidate
    pytest.skip("no modern bash (major >= 4) is installed")


@pytest.mark.parametrize("bash_kind", ["system", "modern"])
@pytest.mark.parametrize("launch", ["pipe", "command"])
@pytest.mark.parametrize("script_name", ["install.sh", "dashboard-ui/public/install.sh"])
def test_script_without_file_selects_web(tmp_path, isolated, script_name, launch, bash_kind):
    bash = _bash(bash_kind, tmp_path, isolated)
    caller = tmp_path / "caller"
    (caller / "bin").mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (caller / "bin/samurai").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"with Path({str(marker)!r}).open('a') as out:\n"
        "    out.write(sys.argv[1] + '\\n')\n",
        encoding="utf-8",
    )
    script = (ROOT / script_name).read_text(encoding="utf-8")
    kwargs = dict(
        cwd=caller, env=isolated, capture_output=True, text=True, timeout=20,
    )
    if launch == "pipe":
        result = subprocess.run([bash], input=script, **kwargs)
    else:
        result = subprocess.run([bash, "-c", script], stdin=subprocess.DEVNULL, **kwargs)

    assert "Web Installer" in result.stdout, result.stdout + result.stderr
    assert "Local Installer" not in result.stdout, result.stdout
    assert not marker.exists() or marker.read_bytes() == b"", result.stdout
