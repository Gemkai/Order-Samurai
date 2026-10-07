"""Runtime dependency contract for the standalone Order Samurai installer.

The installer tests use a fake Python executable, so they prove interpreter
selection and fail-closed behavior without contacting a package index.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

import pytest


ORDER_SAMURAI_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ORDER_SAMURAI_ROOT / "bin" / "install.sh"


FAKE_PYTHON = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    from pathlib import Path
    import shutil
    import sys

    me = Path(sys.argv[0])
    # A copy made by `-m venv` below knows it is a venv by its pyvenv.cfg, like CPython.
    in_venv = (me.parent.parent / "pyvenv.cfg").is_file() or os.environ.get("FAKE_IN_VENV") == "1"

    log_path = Path(os.environ["FAKE_PYTHON_LOG"])
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"exe": str(me), "args": sys.argv[1:]}) + "\\n")

    args = sys.argv[1:]
    marker = Path(os.environ["FAKE_PIP_AUDIT_MARKER"])
    requests_marker = Path(os.environ["FAKE_REQUESTS_MARKER"])
    if args[:1] == ["-c"]:
        code = args[1] if len(args) > 1 else ""
        if "EXTERNALLY-MANAGED" in code:
            managed = os.environ.get("FAKE_EXTERNALLY_MANAGED") == "1" and not in_venv
            raise SystemExit(0 if managed else 1)
        if "import sys, pip" in code:
            cfg = me.parent.parent / "pyvenv.cfg"
            raise SystemExit(1 if "no-pip" in cfg.read_text(encoding="utf-8") else 0)
        if "version_info" in code:
            print("1")
            raise SystemExit(0)
        if "sys.prefix != sys.base_prefix" in code:
            raise SystemExit(0 if in_venv else 1)
        if "import jsonschema" in code:
            raise SystemExit(0 if os.environ.get("FAKE_JSONSCHEMA") == "1" else 1)
        if "import pip_audit" in code:
            available = os.environ.get("FAKE_PIP_AUDIT") == "1" or marker.exists()
            raise SystemExit(0 if available else 1)
        if "import requests" in code:
            available = os.environ.get("FAKE_REQUESTS") == "1" or requests_marker.exists()
            raise SystemExit(0 if available else 1)

    if args[:2] == ["-m", "venv"]:
        if os.environ.get("FAKE_VENV_FAILS") == "1":
            print("Error: ensurepip is not available", file=sys.stderr)
            raise SystemExit(1)
        target = Path(args[-1])
        if "--clear" in args and target.exists():
            shutil.rmtree(target)
        (target / "bin").mkdir(parents=True, exist_ok=True)
        (target / "pyvenv.cfg").write_text("home = fake\\n", encoding="utf-8")
        shutil.copy2(me, target / "bin" / "python")
        raise SystemExit(0)

    if args[:3] == ["-m", "pip", "install"]:
        returncode = int(os.environ.get("FAKE_PIP_RETURN_CODE", "0"))
        if returncode == 0 and os.environ.get("FAKE_INSTALL_STICKS", "1") == "1":
            if args[-1].startswith("pip-audit"):
                marker.touch()
            if args[-1].startswith("requests"):
                requests_marker.touch()
        raise SystemExit(returncode)

    if args and args[0].endswith("first_blood.py"):
        if os.environ.get("FAKE_REQUIRE_PRODUCT_PATH") == "1":
            product_root = str(Path(args[0]).resolve().parents[1])
            python_paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
            raise SystemExit(0 if product_root in python_paths else 1)
        raise SystemExit(0)

    raise SystemExit(0)
    """
)


class DependencyDeclarationTests(unittest.TestCase):

    def test_pip_audit_is_declared_for_dev_and_standalone_runtime_installs(self) -> None:
        project = tomllib.loads(
            (ORDER_SAMURAI_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )

        runtime_spec = next(
            (
                str(requirement)
                for requirement in project["project"]["dependencies"]
                if str(requirement).startswith("pip-audit")
            ),
            None,
        )

        self.assertIsNotNone(
            runtime_spec,
            "the standalone product runtime must declare pip-audit",
        )

        # In the AgenticaOS monorepo this suite also guards the shared CI/dev
        # declaration. The curated public product is intentionally flat and has
        # no parent-level requirements-dev.txt; pyproject.toml is its complete
        # standalone dependency contract, so never walk outside that repo to
        # invent a monorepo root.
        if not (ORDER_SAMURAI_ROOT / "agentica_core").is_dir():
            dev_path = ORDER_SAMURAI_ROOT.parents[1] / "requirements-dev.txt"
            self.assertTrue(dev_path.is_file(), "the monorepo must ship requirements-dev.txt")
            dev_requirements = dev_path.read_text(encoding="utf-8").splitlines()
            dev_spec = next(
                (
                    line.strip()
                    for line in dev_requirements
                    if line.strip().startswith("pip-audit")
                ),
                None,
            )
            self.assertIsNotNone(dev_spec, "the shared dev/CI install must include pip-audit")
            self.assertEqual(
                dev_spec,
                runtime_spec,
                "dev and runtime scanner specs must not drift",
            )


class InstallerRuntimeDependencyTests(unittest.TestCase):

    def _run_installer(
        self,
        *,
        pip_audit_available: bool,
        install_sticks: bool = True,
        requests_available: bool = True,
        in_virtualenv: bool = False,
        require_product_path: bool = False,
        pip_return_code: int = 0,
        externally_managed: bool = False,
        venv_fails: bool = False,
        existing_venv: str | None = None,
        use_samurai_home: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], list[dict], dict[str, Path]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_python = root / "fake-python"
            log_path = root / "python-calls.jsonl"
            marker = root / "pip-audit-installed"
            requests_marker = root / "requests-installed"
            fake_python.write_text(FAKE_PYTHON, encoding="utf-8")
            fake_python.chmod(0o755)
            home = root / "home"
            samurai_home = root / "samurai-home" if use_samurai_home else home / ".samurai"
            venv = samurai_home / "venv"
            if existing_venv in ("healthy", "no-pip"):
                # "no-pip": what a venv creation that failed at ensurepip leaves.
                (venv / "bin").mkdir(parents=True)
                (venv / "pyvenv.cfg").write_text(f"home = fake\n# {existing_venv}\n", encoding="utf-8")
                shutil.copy2(fake_python, venv / "bin" / "python")
            elif existing_venv == "broken":
                # What `brew upgrade python` leaves behind: the venv's interpreter
                # symlink points at a Cellar version that no longer exists.
                (venv / "bin").mkdir(parents=True)
                (venv / "bin" / "python").symlink_to(root / "gone" / "python3.12")

            env = os.environ.copy()
            env.pop("SAMURAI_HOME", None)
            env.update(
                {
                    "HOME": str(home),
                    "PYTHON": str(fake_python),
                    "FAKE_PYTHON_LOG": str(log_path),
                    "FAKE_PIP_AUDIT_MARKER": str(marker),
                    "FAKE_REQUESTS_MARKER": str(requests_marker),
                    "FAKE_JSONSCHEMA": "1",
                    "FAKE_PIP_AUDIT": "1" if pip_audit_available else "0",
                    "FAKE_REQUESTS": "1" if requests_available else "0",
                    "FAKE_INSTALL_STICKS": "1" if install_sticks else "0",
                    "FAKE_PIP_RETURN_CODE": str(pip_return_code),
                    "FAKE_IN_VENV": "1" if in_virtualenv else "0",
                    "FAKE_REQUIRE_PRODUCT_PATH": "1" if require_product_path else "0",
                    "FAKE_VENV_FAILS": "1" if venv_fails else "0",
                    "FAKE_EXTERNALLY_MANAGED": "1" if externally_managed else "0",
                }
            )
            if use_samurai_home:
                env["SAMURAI_HOME"] = str(samurai_home)
            proc = subprocess.run(
                ["bash", str(INSTALLER), "--logs-dir", str(root / "logs")],
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            calls = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            paths = {"base": fake_python, "venv": venv, "venv_python": venv / "bin" / "python"}
            return proc, calls, paths

    @staticmethod
    def _pip_installs(calls: list[dict]) -> list[dict]:
        return [c for c in calls if c["args"][:3] == ["-m", "pip", "install"]]

    @staticmethod
    def _first_blood(calls: list[dict]) -> list[dict]:
        return [c for c in calls if c["args"] and c["args"][0].endswith("first_blood.py")]

    @staticmethod
    def _venv_creations(calls: list[dict]) -> list[dict]:
        return [c for c in calls if c["args"][:2] == ["-m", "venv"]]

    def test_externally_managed_python_installs_into_a_private_venv(self) -> None:
        # Homebrew and distro Pythons are PEP 668 "externally managed": pip refuses
        # to install into them, --user included. A private venv always accepts.
        proc, calls, paths = self._run_installer(
            pip_audit_available=False, externally_managed=True
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            [(c["exe"], c["args"]) for c in self._venv_creations(calls)],
            [(str(paths["base"]), ["-m", "venv", "--clear", str(paths["venv"])])],
        )
        self.assertEqual(
            [(c["exe"], c["args"]) for c in self._pip_installs(calls)],
            [(str(paths["venv_python"]), ["-m", "pip", "install", "--quiet", "pip-audit>=2.7"])],
        )
        self.assertEqual(
            [c["exe"] for c in self._first_blood(calls)], [str(paths["venv_python"])]
        )

    def test_private_venv_defaults_under_home_samurai(self) -> None:
        proc, calls, paths = self._run_installer(
            pip_audit_available=True, externally_managed=True, use_samurai_home=False
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(str(paths["venv"]).endswith("/home/.samurai/venv"))
        self.assertEqual(
            [c["exe"] for c in self._first_blood(calls)], [str(paths["venv_python"])]
        )

    def test_existing_private_venv_is_reused(self) -> None:
        proc, calls, paths = self._run_installer(
            pip_audit_available=True, externally_managed=True, existing_venv="healthy"
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._venv_creations(calls), [])
        self.assertEqual(
            [c["exe"] for c in self._first_blood(calls)], [str(paths["venv_python"])]
        )

    def test_unusable_private_venv_is_rebuilt(self) -> None:
        for state in ("broken", "no-pip"):
            with self.subTest(state=state):
                proc, calls, paths = self._run_installer(
                    pip_audit_available=True, externally_managed=True, existing_venv=state
                )

                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(
                    [c["args"] for c in self._venv_creations(calls)],
                    [["-m", "venv", "--clear", str(paths["venv"])]],
                )
                self.assertEqual(
                    [c["exe"] for c in self._first_blood(calls)], [str(paths["venv_python"])]
                )

    def test_venv_creation_failure_stops_with_guidance(self) -> None:
        proc, calls, _ = self._run_installer(
            pip_audit_available=True, externally_managed=True, venv_fails=True
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("python3-venv", proc.stderr)
        self.assertEqual(self._pip_installs(calls), [])
        self.assertEqual(self._first_blood(calls), [])

    def test_python_override_that_is_a_virtualenv_is_used_directly(self) -> None:
        # ONBOARDING's source install: PYTHON="$PWD/.venv/bin/python" bash bin/install.sh
        proc, calls, paths = self._run_installer(
            pip_audit_available=True,
            requests_available=False,
            in_virtualenv=True,
            externally_managed=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._venv_creations(calls), [])
        self.assertFalse(paths["venv"].exists())
        self.assertEqual(
            [(c["exe"], c["args"]) for c in self._pip_installs(calls)],
            [(str(paths["base"]), ["-m", "pip", "install", "--quiet", "requests>=2.31"])],
        )
        self.assertEqual([c["exe"] for c in self._first_blood(calls)], [str(paths["base"])])

    def test_unmanaged_python_keeps_the_user_site_install(self) -> None:
        # The dashboard API and Dojo run plain python3; on a Python that accepts
        # --user they keep finding the dependencies there.
        proc, calls, paths = self._run_installer(
            pip_audit_available=True,
            requests_available=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._venv_creations(calls), [])
        self.assertFalse(paths["venv"].exists())
        self.assertEqual(
            [(c["exe"], c["args"]) for c in self._pip_installs(calls)],
            [(str(paths["base"]), ["-m", "pip", "install", "--quiet", "--user", "requests>=2.31"])],
        )
        self.assertEqual([c["exe"] for c in self._first_blood(calls)], [str(paths["base"])])

    def test_first_blood_can_import_modules_from_the_product_root(self) -> None:
        proc, calls, _ = self._run_installer(
            pip_audit_available=True,
            require_product_path=True,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self._first_blood(calls))

    def test_zero_exit_install_that_does_not_make_scanner_importable_stops(self) -> None:
        proc, calls, _ = self._run_installer(
            pip_audit_available=False,
            install_sticks=False,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pip-audit>=2.7 is still unavailable", proc.stderr)
        self.assertEqual(self._first_blood(calls), [])

    def test_existing_runtime_dependencies_do_not_invoke_pip(self) -> None:
        proc, calls, _ = self._run_installer(pip_audit_available=True)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._pip_installs(calls), [])


class HookInterpreterIndependenceTests(unittest.TestCase):
    """Hooks run as `python3 <script>` on the system interpreter, never the private
    venv: a venv broken by a Python upgrade must not make Claude Code block every
    tool call. That only holds while the hooks need nothing beyond the stdlib."""

    def test_guard_hook_runs_without_any_site_packages(self) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
            env["HOME"] = tmp
            # -S: no site-packages and no user site, so a pip-only import fails.
            proc = subprocess.run(
                [sys.executable, "-S", str(ORDER_SAMURAI_ROOT / "bin" / "prompt_injection_guard.py")],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)


def _externally_managed_python() -> str | None:
    """A Python 3.11+ outside any venv that carries a PEP 668 EXTERNALLY-MANAGED marker."""
    probe = (
        "import ensurepip, os, sys, sysconfig; "
        "ok = sys.version_info >= (3, 11) and sys.prefix == sys.base_prefix "
        "and os.path.isfile(os.path.join(sysconfig.get_path('stdlib'), 'EXTERNALLY-MANAGED')); "
        "raise SystemExit(0 if ok else 1)"
    )
    candidates = [shutil.which("python3"), "/opt/homebrew/bin/python3",
                  "/usr/local/bin/python3", "/usr/bin/python3"]
    for candidate in dict.fromkeys(c for c in candidates if c and Path(c).exists()):
        try:
            if subprocess.run([candidate, "-c", probe], capture_output=True, timeout=10).returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


@pytest.mark.live_machine
class ExternallyManagedPythonInstallTests(unittest.TestCase):
    """Real-interpreter check: needs a PEP 668 Python (e.g. Homebrew) and network."""

    def test_installer_succeeds_and_reports_on_an_externally_managed_python(self) -> None:
        base = _externally_managed_python()
        if base is None:
            self.skipTest("no PEP 668 externally-managed Python 3.11+ on this machine")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "logs").mkdir()
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("PYTHON", "PIP_", "VIRTUAL_ENV"))}
            env.update({"HOME": str(root), "PYTHON": base,
                        "SAMURAI_HOME": str(root / ".samurai")})
            proc = subprocess.run(
                ["bash", str(INSTALLER), "--logs-dir", str(root / "logs")],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=600, env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
            self.assertIn("Order Samurai -- ", proc.stdout)
            venv_python = root / ".samurai" / "venv" / "bin" / "python"
            imports = subprocess.run(
                [str(venv_python), "-c", "import jsonschema, requests, pip_audit"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(imports.returncode, 0, imports.stderr)


if __name__ == "__main__":
    unittest.main()
