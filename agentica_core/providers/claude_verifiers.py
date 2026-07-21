"""Claude platform verifier provider — binds Order Samurai's `run_checks` functions
into the agentica_core slot-C contract. They already return {status,label,detail},
so no translation is needed. The coupling to Order Samurai lives HERE, not in the kernel.

Order Samurai is reached via the Governance junction (Governance/Order Samurai).
"""
from __future__ import annotations

from pathlib import Path

from . import load_run_checks

# providers -> agentica_core -> Governance
_GOVERNANCE = Path(__file__).resolve().parents[2]
_OS_ROOT = _GOVERNANCE

_IMPORTS = [
    ("execution.verify_path_authority", "run_checks"),
    ("execution.verify_runtime_contract", "run_checks"),
    ("execution.verify_root_hygiene", "run_checks"),
    ("execution.verify_archive_boundaries", "run_checks"),
]


def get_verifiers() -> list:
    return load_run_checks(_OS_ROOT, _IMPORTS)
