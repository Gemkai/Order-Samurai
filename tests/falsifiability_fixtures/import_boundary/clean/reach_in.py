"""The legal shape: put a directory INSIDE the shipped tree on sys.path, import from it.

Fixture data for tests/test_import_boundary.py -- never imported or executed.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    sys.path.insert(0, str(ROOT / "bin"))
    import inside_module
    return inside_module
