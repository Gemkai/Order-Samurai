#!/usr/bin/env python3
"""``order-samurai`` command-line entry point.

Exists so a CI pipeline has something to fail on. Order Samurai's protection has until now been
fail-closed hooks on one workstation: strong for the person running it, nothing a team's pipeline
could adopt. ``order-samurai audit`` runs the repo-policy verifiers headless and exits non-zero when
any of them FAIL.

Scope: the POLICY families only -- the verifiers that judge the repository. The runtime-health
families (telemetry freshness, local-LLM liveness, daemon state, exec-chain integrity) are
deliberately excluded: they describe a live workstation, and in a clean CI checkout they would fail
for reasons that say nothing about the code under review. A gate that cries wolf gets disabled, and
a disabled gate protects nothing. Runtime health remains ``doctor``'s job -- run
``python3 execution/doctor.py`` for that.

Every check family here already exposes ``run_checks()`` and ``summarize()``; this module composes
them and owns no verification logic of its own.
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
from execution import verify_live_sources  # noqa: E402
from execution import verify_no_stale_paths  # noqa: E402
from execution import verify_path_authority  # noqa: E402
from execution import verify_root_hygiene  # noqa: E402

__version__ = "1.0.0"  # canonical source: pyproject.toml [project].version

# (label, module). Order is presentation only -- every family runs regardless of earlier failures,
# because a pipeline that reports one problem per push wastes a round trip per problem.
POLICY_FAMILIES = [
    ("path-authority", verify_path_authority),
    ("stale-paths", verify_no_stale_paths),
    ("root-hygiene", verify_root_hygiene),
    ("agentica-root-hygiene", verify_agentica_root_hygiene),
    ("archive-boundaries", verify_archive_boundaries),
    ("live-sources", verify_live_sources),
]

_EXIT_OK = 0
_EXIT_FINDINGS = 1
_EXIT_ERROR = 2


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
        print("For workstation runtime health (daemons, telemetry, local LLM), run:")
        print("  python3 execution/doctor.py")
        return _EXIT_OK if known.want_help else _EXIT_ERROR

    if known.command == "version":
        print(f"order-samurai {__version__}")
        return _EXIT_OK

    return run_audit(rest)


if __name__ == "__main__":
    raise SystemExit(main())
