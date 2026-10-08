"""The 2.1.3 shape: reach into the developer's home, import, swallow the failure.

Fixture data for tests/test_import_boundary.py -- never imported or executed.
"""
import sys
from pathlib import Path


def dead_rules():
    sys.path.insert(0, str(Path.home()))
    try:
        import principle_audit
    except Exception:
        return None
    return principle_audit.PATTERNS
