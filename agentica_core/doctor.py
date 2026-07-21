"""The platform-neutral doctor — resolves a platform, runs its verifiers through the
slot-C contract, prints a report, and returns an exit code (1 if any FAIL, else 0).

Usage:  python -m agentica_core.doctor [platform]
        (run from the Governance dir so module names don't shadow stdlib)
"""
from __future__ import annotations

import sys

from . import verify_layers, verify_secrets
from .adapter import AmbiguousPlatform, PlatformUnavailable, resolve_platform
from .verifiers import load_verifiers, run_all, summarize


def run_doctor(name: str | None = None) -> int:
    print("Agentica Doctor")
    print("=" * 44)

    # Always-on, platform-independent: the Agentica layer boundary invariants.
    print("Agentica layer integrity:")
    layer_results = verify_layers.run_checks()
    for r in layer_results:
        print(f"[{r['status']}] {r['label']}: {r['detail']}")

    # Always-on, platform-independent: the Sword pillar (secret scan of the control plane).
    print("-" * 44)
    print("Security (Sword):")
    security_results = verify_secrets.run_checks()
    for r in security_results:
        print(f"[{r['status']}] {r['label']}: {r['detail']}")

    try:
        platform = resolve_platform(name)
    except (PlatformUnavailable, AmbiguousPlatform) as exc:
        print(f"[doctor] cannot resolve platform: {exc}")
        return 2

    print("-" * 44)
    print(f"platform: {platform.name}  ({platform.runtime_root})")
    try:
        verifiers = load_verifiers(platform.name)
    except Exception as exc:  # provider import/exec failure is a governance failure
        print(f"[FAIL] verifier provider failed to load: {exc!r}")
        return 1

    if not verifiers:
        print("[WARN] no verifiers bound for this platform yet")
        platform_results: list = []
    else:
        platform_results = run_all(verifiers)
        for r in platform_results:
            print(f"[{r['status']}] {r['label']}: {r['detail']}")

    counts, exit_code = summarize(layer_results + security_results + platform_results)
    print("=" * 44)
    print(f"Summary: OK={counts['OK']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    return run_doctor(argv[0] if argv else None)


if __name__ == "__main__":
    raise SystemExit(main())
