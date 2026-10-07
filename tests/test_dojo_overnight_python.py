"""The Nightly Dojo's default VALIDATE_CMD must use the interpreter that has the deps.

On PEP 668 "externally managed" Pythons (Homebrew, Debian/Ubuntu) bin/install.sh puts
jsonschema/requests into ${SAMURAI_HOME:-~/.samurai}/venv; agentica_core/aggregate.py
imports jsonschema, so a plain `python` validate command fails there. These tests run
bin/dojo_overnight.sh for one dry-run cycle against a scratch repo with a stub
`claude` that records the environment and --allowedTools it was launched with.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ORDER_SAMURAI_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name == "nt", reason="bash entrypoint")

STUB_CLAUDE = textwrap.dedent(
    """\
    #!/bin/sh
    out="$STUB_CLAUDE_DIR"
    printf '%s' "$DOJO_VALIDATE_CMD" > "$out/validate_cmd"
    prev=""
    for a in "$@"; do
      [ "$prev" = "--allowedTools" ] && printf '%s' "$a" > "$out/allowed_tools"
      prev="$a"
    done
    echo '{"result":"stub"}'
    """
)


def _write_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def _run_dojo(tmp_path: Path, extra_env: dict[str, str] | None = None,
              dojo_env: str | None = None) -> tuple[str, str]:
    bin_dir = tmp_path / "pkg" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("dojo_overnight.sh", "lib_pro_gate.sh"):
        shutil.copy2(ORDER_SAMURAI_ROOT / "bin" / name, bin_dir / name)

    samurai_home = tmp_path / "samurai"
    samurai_home.mkdir(exist_ok=True)
    subprocess.run(["bash", str(ORDER_SAMURAI_ROOT / "bin" / "make_dev_license.sh")],
                   env=dict(os.environ, SAMURAI_HOME=str(samurai_home)),
                   check=True, capture_output=True, timeout=30)

    repo = tmp_path / "repo"
    (repo / "prompts").mkdir(parents=True)
    (repo / "prompts" / "dojo_cycle.md").write_text("stub prompt\n")
    _write_exe(repo / "bin" / "keiko_improvement.py", "print('keiko: stub')\n")
    if dojo_env is not None:
        (repo / "dojo.env").write_text(dojo_env)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-c", "commit.gpgsign=false", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
    subprocess.run([*git, "add", "-A"], check=True, timeout=30)
    subprocess.run([*git, "commit", "-qm", "init"], check=True, timeout=30)

    stub_dir = tmp_path / "stub"
    _write_exe(stub_dir / "claude", STUB_CLAUDE)

    env = {k: v for k, v in os.environ.items() if k != "VALIDATE_CMD"}
    env.update(
        PATH=f"{stub_dir}{os.pathsep}{env['PATH']}",
        SAMURAI_HOME=str(samurai_home),
        STUB_CLAUDE_DIR=str(stub_dir),
        REPO_DIR=str(repo),
        DOJO_DRYRUN="1",
        COOLDOWN="0",
    )
    env.update(extra_env or {})
    res = subprocess.run(["bash", str(bin_dir / "dojo_overnight.sh")], cwd=repo, env=env,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    return ((stub_dir / "validate_cmd").read_text(),
            (stub_dir / "allowed_tools").read_text())


def test_default_validate_cmd_uses_the_private_venv_python(tmp_path):
    venv_py = _write_exe(tmp_path / "samurai" / "venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    quoted = shlex.quote(str(venv_py))

    validate_cmd, allowed = _run_dojo(tmp_path)

    assert validate_cmd == (f"{quoted} execution/doctor.py && "
                            f"{quoted} agentica_core/aggregate.py")
    # The cycle agent must be allowed to run the command it is handed.
    assert f"Bash({quoted}:*)" in allowed.split(",")


@pytest.mark.parametrize("venv_exit", [None, 1], ids=["no-venv", "broken-venv"])
def test_default_validate_cmd_falls_back_to_python3(tmp_path, venv_exit):
    if venv_exit is not None:
        _write_exe(tmp_path / "samurai" / "venv" / "bin" / "python", f"#!/bin/sh\nexit {venv_exit}\n")

    validate_cmd, _ = _run_dojo(tmp_path)

    assert validate_cmd == "python3 execution/doctor.py && python3 agentica_core/aggregate.py"


def test_explicit_validate_cmd_is_kept(tmp_path):
    _write_exe(tmp_path / "samurai" / "venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")

    validate_cmd, _ = _run_dojo(tmp_path, {"VALIDATE_CMD": "python execution/doctor.py"})

    assert validate_cmd == "python execution/doctor.py"


def test_samurai_home_from_dojo_env_selects_the_venv(tmp_path):
    other = tmp_path / "from-dojo-env"
    venv_py = _write_exe(other / "venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")

    validate_cmd, _ = _run_dojo(tmp_path, dojo_env=f'SAMURAI_HOME="{other}"\n')

    assert validate_cmd.startswith(f"{shlex.quote(str(venv_py))} execution/doctor.py")


def test_escaped_venv_path_is_not_added_to_allowed_tools(tmp_path):
    # A shell-escaped path cannot be matched literally by the --allowedTools rule,
    # so the command still uses the venv but no unmatchable rule is emitted.
    spaced = tmp_path / "sam urai"
    venv_py = _write_exe(spaced / "venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")

    validate_cmd, allowed = _run_dojo(tmp_path, dojo_env=f'SAMURAI_HOME="{spaced}"\n')

    assert validate_cmd.startswith(str(venv_py).replace(" ", "\\ ") + " execution/doctor.py")
    assert not [rule for rule in allowed.split(",") if "venv" in rule]
