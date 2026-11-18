"""Exercise the downloaded installer in an empty HOME, without network or live state."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = True
RUNTIME = ("budget_ledger.json", "vibe_alignment.json", "calibration_coefficients.json")
SEEDS = {name: json.dumps({"seed": name}).encode() for name in RUNTIME}
POPULATED = {
    "budget_ledger.json": json.dumps({"date": datetime.now(timezone.utc).date().isoformat(),
                                      "spent_usd": 12.75, "daily_limit_usd": 10, "cycles": 8}).encode(),
    "vibe_alignment.json": b'{"score":82,"sample_count":17}\n',
    "calibration_coefficients.json": b'{"operations":{"calibrated":true,"sample_count":23}}\n',
}
pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("curl", "unzip", "shasum")),
    reason="installer requires curl, unzip and shasum",
)


def _setup(tmp_path):
    home = tmp_path / "Buyer Home"
    home.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    archive = site / "order-samurai-core.zip"
    # Observe the state at the instant install/doctor run, before they can repair it.
    cli = """import json, os, sys
from pathlib import Path
core = Path(__file__).resolve().parents[1]
if sys.argv[1] in ('install', 'doctor'):
    expected = json.loads(Path(os.environ['EXPECTED']).read_text())
    for relative, value in expected.items():
        assert (core / relative).read_bytes().hex() == value, relative
    with Path(os.environ['CALLS']).open('a') as out:
        out.write(sys.argv[1] + '\\n')
"""
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("bin/samurai", cli)
        zf.writestr("bin/install.sh", "exit 0\n")
        zf.writestr("version.txt", "new code\n")
        zf.writestr("state/kill_chain_taxonomy.json", '{"policy":"new"}')
        for name, value in SEEDS.items():
            zf.writestr("state/" + name, value)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (site / "order-samurai-core.zip.sha256").write_text(f"{digest}  order-samurai-core.zip\n")
    script = tmp_path / "install.sh"
    shutil.copy2(ROOT / "install.sh", script)
    env = {
        "HOME": str(home), "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path), "SAMURAI_NO_PROMPT": "1", "SAMURAI_NO_UPDATE_CHECK": "1",
        "OS_BASE_URL": site.as_uri(), "OS_CORE_BASE_URL": site.as_uri(), "OS_SHA_RETRY_DELAY": "0",
        "EXPECTED": str(tmp_path / "expected.json"), "CALLS": str(tmp_path / "calls"),
    }
    return home / ".samurai", script, env, archive


def _run(script, env, expected):
    Path(env["EXPECTED"]).write_text(json.dumps({"state/" + k: v.hex() for k, v in expected.items()}))
    return subprocess.run(["/bin/bash", str(script)], cwd=script.parent, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def _write(core, files):
    for relative, value in files.items():
        path = core / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        path.chmod(0o600)


def test_clean_install_initializes_all_runtime_seeds(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    result = _run(script, env, SEEDS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(env["CALLS"]).read_text().splitlines() == ["install", "doctor"]
    for name, value in SEEDS.items():
        assert (samurai / "core/state" / name).read_bytes() == value


@pytest.mark.parametrize("missing", [None, *RUNTIME])
def test_upgrade_keeps_populated_state_and_initializes_only_missing_files(tmp_path, missing):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    preserved = {name: value for name, value in POPULATED.items() if name != missing}
    _write(core, {"state/" + k: v for k, v in preserved.items()})
    _write(core, {"version.txt": b"old code", "state/kill_chain_taxonomy.json": b"old policy",
                  "meditation.env": b"MEDITATION_BUDGET=5\n", "dojo.env": b"DOJO_X=1\n"})
    _write(samurai, {"license.json": b'{"tier":"pro","key":"TEST-ONLY"}',
                     "settings.json": b'{"user_preference":"keep me"}'})
    expected = {**SEEDS, **preserved}
    result = _run(script, env, expected)
    assert result.returncode == 0, result.stdout + result.stderr
    for name, value in expected.items():
        assert (core / "state" / name).read_bytes() == value
    for name in preserved:
        assert (core / "state" / name).stat().st_mode & 0o777 == 0o600
    assert (core / "version.txt").read_text() == "new code\n"
    assert (core / "state/kill_chain_taxonomy.json").read_text() == '{"policy":"new"}'
    assert (samurai / "license.json").read_bytes() == b'{"tier":"pro","key":"TEST-ONLY"}'
    assert (samurai / "settings.json").read_bytes() == b'{"user_preference":"keep me"}'
    assert (core / "meditation.env").read_bytes() == b"MEDITATION_BUDGET=5\n"
    assert (core / "dojo.env").read_bytes() == b"DOJO_X=1\n"
    if not PRODUCT:
        backups = list(samurai.glob("core.bak-*"))
        assert len(backups) == 1
        for name, value in preserved.items():
            assert (backups[0] / "state" / name).read_bytes() == value


def test_existing_malformed_state_is_not_silently_reset(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    existing = {name: b"{partial or invalid JSON\n" for name in RUNTIME}
    _write(samurai / "core", {"state/" + k: v for k, v in existing.items()})
    result = _run(script, env, existing)
    assert result.returncode == 0, result.stdout + result.stderr


def test_bad_checksum_leaves_existing_state_untouched(tmp_path):
    samurai, script, env, archive = _setup(tmp_path)
    _write(samurai / "core", {"state/" + k: v for k, v in POPULATED.items()})
    archive.write_bytes(b"corrupt download")
    result = _run(script, env, POPULATED)
    assert result.returncode != 0
    assert "CHECKSUM MISMATCH" in result.stderr
    assert not Path(env["CALLS"]).exists()
    for name, value in POPULATED.items():
        assert (samurai / "core/state" / name).read_bytes() == value
    assert not list(samurai.glob("core.bak-*"))


def test_failed_extraction_never_replaces_existing_runtime_state(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    shim = tmp_path / "tools"
    shim.mkdir()
    unzip = shim / "unzip"
    # Exercise a failure after files were actually extracted, not just a rejected ZIP.
    unzip.write_text(f'#!/bin/bash\n"{shutil.which("unzip")}" "$@"\nexit 42\n')
    unzip.chmod(0o755)
    env["PATH"] = str(shim) + ":" + env["PATH"]
    result = _run(script, env, POPULATED)
    assert result.returncode == 42, result.stdout + result.stderr
    assert not Path(env["CALLS"]).exists()
    for name, value in POPULATED.items():
        assert (core / "state" / name).read_bytes() == value


def test_upgrade_keeps_runtime_files_in_place_for_running_writers(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    ledger = core / "state/budget_ledger.json"
    inode = ledger.stat().st_ino
    updated = {**POPULATED, "budget_ledger.json": POPULATED["budget_ledger.json"].replace(b"12.75", b"15.75")}
    shim = tmp_path / "tools"
    shim.mkdir()
    writer = shim / "writer.py"
    writer.write_text("from pathlib import Path\n" +
                      f"Path({str(ledger)!r}).write_bytes({updated['budget_ledger.json']!r})\n")
    unzip = shim / "unzip"
    unzip.write_text(f'#!/bin/bash\npython3 "{writer}"\nexec "{shutil.which("unzip")}" "$@"\n')
    unzip.chmod(0o755)
    env["PATH"] = str(shim) + ":" + env["PATH"]
    result = _run(script, env, updated)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ledger.read_bytes() == updated["budget_ledger.json"]
    assert ledger.stat().st_ino == inode


@pytest.mark.parametrize("missing_ledger", [False, True])
def test_writer_created_or_replaced_ledger_during_extraction_wins(tmp_path, missing_ledger):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    files = {"state/" + k: v for k, v in POPULATED.items()
             if not missing_ledger or k != "budget_ledger.json"}
    _write(core, files)
    ledger = core / "state/budget_ledger.json"
    updated = {**POPULATED, "budget_ledger.json": POPULATED["budget_ledger.json"].replace(b"12.75", b"16.25")}
    shim = tmp_path / "tools"
    shim.mkdir()
    writer = shim / "writer.py"
    writer.write_text("from pathlib import Path\n" +
                      f"p=Path({str(ledger)!r})\n" +
                      f"p.with_suffix('.tmp').write_bytes({updated['budget_ledger.json']!r})\n" +
                      "p.with_suffix('.tmp').replace(p)\n")
    unzip = shim / "unzip"
    unzip.write_text(f'#!/bin/bash\npython3 "{writer}"\nexec "{shutil.which("unzip")}" "$@"\n')
    unzip.chmod(0o755)
    env["PATH"] = str(shim) + ":" + env["PATH"]
    result = _run(script, env, updated)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ledger.read_bytes() == updated["budget_ledger.json"]


@pytest.mark.parametrize("failure", ["zip", "sidecar"])
def test_download_failure_leaves_core_and_entitlement_untouched(tmp_path, failure):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    _write(core, {"version.txt": b"old code", "dashboard-ui/public/reports/audit__local.md": b"local report"})
    _write(samurai, {"license.json": b"test license", "settings.json": b"test settings"})
    before = {p.relative_to(samurai): p.read_bytes() for p in samurai.rglob('*') if p.is_file()}
    shim = tmp_path / "tools"
    shim.mkdir()
    curl = shim / "curl"
    suffix = "*.zip" if failure == "zip" else "*.sha256"
    curl.write_text(f'#!/bin/bash\ncase "$2" in {suffix}) exit 22 ;; esac\nexec "{shutil.which("curl")}" "$@"\n')
    curl.chmod(0o755)
    env["PATH"] = str(shim) + ":" + env["PATH"]
    result = _run(script, env, POPULATED)
    assert result.returncode != 0
    assert not Path(env["CALLS"]).exists()
    assert not list(samurai.glob('core.bak-*'))
    assert {p.relative_to(samurai): p.read_bytes() for p in samurai.rglob('*') if p.is_file()} == before


def test_interrupted_extraction_keeps_runtime_state_available(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    shim = tmp_path / "tools"
    shim.mkdir()
    unzip = shim / "unzip"
    unzip.write_text(f'#!/bin/bash\n"{shutil.which("unzip")}" "$@"\nkill -TERM "$PPID"\n')
    unzip.chmod(0o755)
    env["PATH"] = str(shim) + ":" + env["PATH"]
    result = _run(script, env, POPULATED)
    assert result.returncode != 0, result.stdout + result.stderr
    assert not Path(env["CALLS"]).exists()
    for name, value in POPULATED.items():
        assert (core / 'state' / name).read_bytes() == value


def test_unshipped_runtime_and_report_history_survive_upgrade(tmp_path):
    samurai, script, env, _ = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    local = {"state/DOJO_STATE.json": b'{"cycles":19}',
             "state/logs/events.jsonl": b'{"event":"test-only"}\n',
             "dashboard-ui/public/reports/audit__local.md": b"local report",
             "dashboard-ui/public/reports/index.json": b'[{"kind":"audit","file":"audit__local.md"}]'}
    _write(core, local)
    _write(core, {"dashboard-ui/public/reports/2026-W28__claude.md": b"legacy vendor sample"})
    result = _run(script, env, POPULATED)
    assert result.returncode == 0, result.stdout + result.stderr
    for name, value in local.items():
        assert (core/name).read_bytes() == value
    assert not (core/'dashboard-ui/public/reports/2026-W28__claude.md').exists()


@pytest.mark.parametrize("entrypoint", ["missing", "directory", "empty"])
def test_incomplete_archive_cannot_reuse_old_cli_or_change_install(tmp_path, entrypoint):
    samurai, script, env, archive = _setup(tmp_path)
    core = samurai / "core"
    _write(core, {"state/" + k: v for k, v in POPULATED.items()})
    _write(core, {"bin/samurai": b"print('OLD CLI MUST NOT RUN')\n", "version.txt": b"old code"})
    before = {p.relative_to(samurai): p.read_bytes() for p in samurai.rglob('*') if p.is_file()}
    with zipfile.ZipFile(archive) as z:
        members = {name: z.read(name) for name in z.namelist() if name != 'bin/samurai'}
    if entrypoint == "directory":
        members['bin/samurai/'] = b''
    elif entrypoint == "empty":
        members['bin/samurai'] = b''
    with zipfile.ZipFile(archive, 'w') as z:
        for name, value in members.items():
            z.writestr(name, value)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f"{digest}  order-samurai-core.zip\n")
    result = _run(script, env, POPULATED)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "OLD CLI MUST NOT RUN" not in result.stdout
    assert not Path(env["CALLS"]).exists()
    assert not list(samurai.glob('core.bak-*'))
    assert {p.relative_to(samurai): p.read_bytes() for p in samurai.rglob('*') if p.is_file()} == before
