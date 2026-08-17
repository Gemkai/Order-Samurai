#!/usr/bin/env python3
"""``order-samurai`` command-line entry point.

Exists so a CI pipeline has something to fail on. Order Samurai's protection has until now been
fail-closed hooks on one workstation: strong for the person running it, nothing a team's pipeline
could adopt. ``order-samurai audit`` runs the repo-policy verifiers headless and exits non-zero when
any of them FAIL.

Scope: the POLICY families only -- the verifiers that judge the repository. The runtime-health
families (live-source payloads, telemetry freshness, local-LLM liveness, daemon state, exec-chain
integrity) are deliberately excluded: they describe a live workstation, and in a clean CI checkout
they would fail for reasons that say nothing about the code under review. A gate that cries wolf
gets disabled, and a disabled gate protects nothing. Runtime health remains ``doctor``'s job -- run
``python3 execution/doctor.py`` for that.

Every check family here already exposes ``run_checks()`` and ``summarize()``; this module composes
them and owns no verification logic of its own.

Requires a checkout. The verifiers enforce the contracts in ``config/``, which ``pip install`` does
not ship, so ``audit`` refuses (exit 2) rather than reporting on its own install directory. See
``policy_contracts_unavailable()`` for why refusing beats the obvious alternative.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from execution import verify_agentica_root_hygiene  # noqa: E402
from execution import verify_archive_boundaries  # noqa: E402
from execution import verify_no_stale_paths  # noqa: E402
from execution import verify_path_authority  # noqa: E402
from execution import verify_root_hygiene  # noqa: E402
from execution.runtime_paths import ROOT_HYGIENE_POLICY_PATH  # noqa: E402

__version__ = "0.1.0"

#: What makes a directory an Order Samurai pack: it carries the policy contracts.
#: Used only to locate a checkout for the operator message -- never to import from.
_PACK_MARKER = Path("config") / "root_hygiene_policy.json"

# (label, module). Order is presentation only -- every family runs regardless of earlier failures,
# because a pipeline that reports one problem per push wastes a round trip per problem.
POLICY_FAMILIES = [
    ("path-authority", verify_path_authority),
    ("stale-paths", verify_no_stale_paths),
    ("root-hygiene", verify_root_hygiene),
    ("agentica-root-hygiene", verify_agentica_root_hygiene),
    ("archive-boundaries", verify_archive_boundaries),
]

_EXIT_OK = 0
_EXIT_FINDINGS = 1
_EXIT_ERROR = 2


def find_pack_root(start: Path | None = None) -> Path | None:
    """Nearest ancestor of `start` (default: cwd) carrying the policy contracts.

    Locates a checkout so the operator message can name it. Deliberately NOT used
    to add that directory to sys.path: importing verifier code from a directory
    chosen by the working directory would hand code execution to whoever controls
    the tree you happen to be standing in, which is not a trade a governance tool
    should make to save a `cd`.
    """
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / _PACK_MARKER).is_file():
            return candidate
    return None


def policy_contracts_unavailable() -> str | None:
    """None when the verifiers can see their policy; else the operator message.

    `pip install` ships only the `execution` and `agentica_core` packages, so an
    installed console script resolves REPO_ROOT to site-packages, where config/
    does not exist. Every policy family then reports its contract "missing" and
    the run exits 1 -- a verdict on the tool's own install directory, dressed as a
    verdict on a repository.

    Refusing is not conservatism; the alternative is measurably worse. Copying
    config/ into site-packages (the obvious "fix") clears those FAILs and makes
    path-authority report OK across "the Governance code surface" while actually
    scanning site-packages and silently skipping two of its five declared scan
    paths, which do not ship. A false clean bill of health from a security tool is
    the one output worse than no output.

    NOTE for whoever ships config/ as package data: this guard only asks whether
    the policy is visible. It stops protecting the moment config/ is packaged, so
    that change MUST land together with target resolution (auditing the repository
    the operator means, not the install directory) -- never before it.
    """
    if ROOT_HYGIENE_POLICY_PATH.is_file():
        return None

    lines = [
        "order-samurai: cannot audit -- the policy contracts are not present at",
        f"  {ROOT_HYGIENE_POLICY_PATH.parent}",
        "",
        "The installed package ships the verifiers but not the config/ contracts",
        "they enforce, so this command can only describe its own install directory.",
        "Run the audit from an Order Samurai checkout instead:",
    ]
    found = find_pack_root()
    if found is not None:
        lines += ["", f'  cd "{found}" && python3 -m execution.cli audit']
    else:
        lines += [
            "",
            "  cd <order-samurai-checkout> && python3 -m execution.cli audit",
            "",
            "No checkout was found above the current directory.",
        ]
    return "\n".join(lines)


def _result_label(result: dict) -> str:
    """Verifier families disagree on the key: most emit `label`, root-hygiene emits `name`."""
    return str(result.get("label") or result.get("name") or "(unlabelled)")


def collect(families=None) -> tuple[list[dict], dict[str, int]]:
    """Run every policy family. Returns (flat results, status counts).

    A family that raises is reported as a FAIL rather than being allowed to abort the run: a
    verifier crashing is itself a finding, and swallowing it would turn a broken gate into a
    silent pass -- the exact failure mode the canary work elsewhere in this repo exists to catch.

    `families` resolves at call time rather than as a default argument: a default would bind the
    module-level list once at import and quietly ignore any later reassignment.
    """
    if families is None:
        families = POLICY_FAMILIES
    results: list[dict] = []
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}

    for family_name, module in families:
        try:
            family_results = module.run_checks()
        except Exception as exc:  # noqa: BLE001 - a crashing verifier is a finding, not a stop
            family_results = [
                {
                    "status": "FAIL",
                    "label": f"{family_name}.verifier-crashed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ]

        for result in family_results:
            status = str(result.get("status", "FAIL")).upper()
            if status not in counts:
                status = "FAIL"
            counts[status] += 1
            results.append(
                {
                    "family": family_name,
                    "status": status,
                    "label": _result_label(result),
                    "detail": str(result.get("detail", "")),
                }
            )

    return results, counts


def _print_text(results: list[dict], counts: dict[str, int], quiet: bool) -> None:
    if not quiet:
        print("Order Samurai Audit")
        print("-------------------")
        for result in results:
            print(f"[{result['status']}] {result['family']}.{result['label']}: {result['detail']}")
        print("-------------------")
    else:
        for result in results:
            if result["status"] == "FAIL":
                print(f"[FAIL] {result['family']}.{result['label']}: {result['detail']}")
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")


def run_audit(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="order-samurai audit",
        description="Run the repo-policy verifiers headless. Exits non-zero on any FAIL.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text for humans (default), json for machines",
    )
    parser.add_argument(
        "--warn-as-error",
        action="store_true",
        help="also exit non-zero when a verifier reports WARN (stricter gate)",
    )
    parser.add_argument("--quiet", action="store_true", help="print only FAIL lines and the summary")
    args = parser.parse_args(argv)

    # Before any verdict: a run that cannot see its policy must say so and stop,
    # not print a summary about wherever it happens to be installed.
    unavailable = policy_contracts_unavailable()
    if unavailable is not None:
        print(unavailable, file=sys.stderr)
        return _EXIT_ERROR

    results, counts = collect()

    if args.format == "json":
        print(json.dumps({"summary": counts, "results": results}, indent=2))
    else:
        _print_text(results, counts, args.quiet)

    if counts["FAIL"] > 0:
        return _EXIT_FINDINGS
    if args.warn_as_error and counts["WARN"] > 0:
        return _EXIT_FINDINGS
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = argparse.ArgumentParser(
        prog="order-samurai",
        description="Order Samurai - governance and security layer for autonomous coding agents.",
        add_help=False,
    )
    parser.add_argument("command", nargs="?", choices=("audit", "version"), help="subcommand")
    parser.add_argument("-h", "--help", action="store_true", dest="want_help")
    known, rest = parser.parse_known_args(argv)

    if known.want_help or known.command is None:
        print("usage: order-samurai <command> [options]")
        print()
        print("commands:")
        print("  audit     run the repo-policy verifiers; exit non-zero on any FAIL")
        print("  version   print the installed version")
        print()
        print("Run audit from an Order Samurai checkout: it enforces the contracts in")
        print("config/, which the installed package does not ship.")
        print()
        print("For workstation runtime health (daemons, telemetry, local LLM), run:")
        print("  python3 execution/doctor.py")
        return _EXIT_OK if known.want_help else _EXIT_ERROR

    if known.command == "version":
        print(f"order-samurai {__version__}")
        return _EXIT_OK

    return run_audit(rest)


if __name__ == "__main__":
    raise SystemExit(main())
