"""Tests for the LLM-verdict parsing in audit_remediation_patch.main().

The 'approved' key in the gateway's JSON verdict is only declared in the
response_schema's `required` list -- nothing constrains its JSON type. main()
must treat it as approved ONLY when it is the real boolean True, not any
other truthy value (a stringified "false", "no", a non-empty list, ...).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution import audit_remediation_patch as arp

# A patch that clears both the path-scope gate and the static checks, so the
# only thing left to determine main()'s return code is the LLM verdict.
_CLEAN_PATCH = (
    "--- a/Governance/agentica_core/insights.py\n"
    "+++ b/Governance/agentica_core/insights.py\n"
    "@@ -1,1 +1,1 @@\n"
    "-old\n"
    "+new\n"
)


def _run_main_with_verdict(tmp_path, monkeypatch, verdict: dict) -> int:
    patch_file = tmp_path / "test.patch"
    patch_file.write_text(_CLEAN_PATCH)
    monkeypatch.setattr(arp.gateway, "generate_text", lambda **kwargs: json.dumps(verdict))
    monkeypatch.setattr(sys, "argv", ["audit_remediation_patch.py", "--patch", str(patch_file)])
    return arp.main()


def test_stringified_false_approved_is_rejected(tmp_path, monkeypatch):
    # "false" is a non-empty string -> truthy in Python -> must NOT pass.
    rc = _run_main_with_verdict(
        tmp_path, monkeypatch,
        {"approved": "false", "failures": ["CORS wildcard '*'"], "reason": "rejected"},
    )
    assert rc == 1


def test_real_boolean_true_is_approved(tmp_path, monkeypatch):
    rc = _run_main_with_verdict(
        tmp_path, monkeypatch,
        {"approved": True, "failures": [], "reason": "clean"},
    )
    assert rc == 0


def test_real_boolean_false_is_rejected(tmp_path, monkeypatch):
    rc = _run_main_with_verdict(
        tmp_path, monkeypatch,
        {"approved": False, "failures": ["unsafe subprocess"], "reason": "rejected"},
    )
    assert rc == 1
