"""Runtime dependency contract for the standalone Order Samurai installer.

The installer tests use a fake Python executable, so they prove interpreter
selection and fail-closed behavior without contacting a package index.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path


ORDER_SAMURAI_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ORDER_SAMURAI_ROOT / "bin" / "install.sh"


FAKE_PYTHON = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import os
    from pathlib import Path
    import sys

    log_path = Path(os.environ["FAKE_PYTHON_LOG"])
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sys.argv[1:]) + "\\n")

    args = sys.argv[1:]
    marker = Path(os.environ["FAKE_PIP_AUDIT_MARKER"])
    if args[:1] == ["-c"]:
        code = args[1] if len(args) > 1 else ""
        if "version_info" in code:
            print("1")
            raise SystemExit(0)
        if "import jsonschema" in code:
            raise SystemExit(0 if os.environ.get("FAKE_JSONSCHEMA") == "1" else 1)
        if "import pip_audit" in code:
            available = os.environ.get("FAKE_PIP_AUDIT") == "1" or marker.exists()
            raise SystemExit(0 if available else 1)

    if args[:3] == ["-m", "pip", "install"]:
        returncode = int(os.environ.get("FAKE_PIP_RETURN_CODE", "0"))
        if returncode == 0 and os.environ.get("FAKE_INSTALL_STICKS", "1") == "1":
            marker.touch()
        raise SystemExit(returncode)

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
        pip_return_code: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_python = root / "fake-python"
            log_path = root / "python-calls.jsonl"
            marker = root / "pip-audit-installed"
            fake_python.write_text(FAKE_PYTHON, encoding="utf-8")
            fake_python.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PYTHON": str(fake_python),
                    "FAKE_PYTHON_LOG": str(log_path),
                    "FAKE_PIP_AUDIT_MARKER": str(marker),
                    "FAKE_JSONSCHEMA": "1",
                    "FAKE_PIP_AUDIT": "1" if pip_audit_available else "0",
                    "FAKE_INSTALL_STICKS": "1" if install_sticks else "0",
                    "FAKE_PIP_RETURN_CODE": str(pip_return_code),
                }
            )
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
            return proc, calls

    def test_missing_pip_audit_is_installed_with_the_selected_interpreter(self) -> None:
        proc, calls = self._run_installer(pip_audit_available=False)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        pip_installs = [call for call in calls if call[:3] == ["-m", "pip", "install"]]
        self.assertEqual(
            pip_installs,
            [["-m", "pip", "install", "--quiet", "--user", "pip-audit>=2.7"]],
        )
        self.assertTrue(any(call and call[0].endswith("first_blood.py") for call in calls))

    def test_zero_exit_install_that_does_not_make_scanner_importable_stops(self) -> None:
        proc, calls = self._run_installer(
            pip_audit_available=False,
            install_sticks=False,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("pip-audit>=2.7 is still unavailable", proc.stderr)
        self.assertFalse(any(call and call[0].endswith("first_blood.py") for call in calls))

    def test_existing_runtime_dependencies_do_not_invoke_pip(self) -> None:
        proc, calls = self._run_installer(pip_audit_available=True)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(any(call[:3] == ["-m", "pip", "install"] for call in calls))


if __name__ == "__main__":
    unittest.main()
