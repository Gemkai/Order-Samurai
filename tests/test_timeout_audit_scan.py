"""Tests for the Unbounded_Wait_Count scanner (execution/timeout_audit_scan.py).

Two of these pin bugs found by precision-checking the scanner's own output on
2026-08-04, before wiring its count into the dashboard:

  * `test_timeout_beyond_six_lines_is_not_a_finding` -- the original fixed
    6-line lookahead reported every long multi-line call as untimed. All 7 of
    its runtime findings were false positives (precision 0/7).
  * `test_mention_in_comment_or_docstring_is_not_a_finding` -- the checks match
    raw text, so prose *describing* an untimed call was itself reported as one.

Both are the same failure class the metric exists to catch: an instrument whose
reading cannot be trusted. A scanner that reports 0 is only meaningful if it
provably reports non-zero on a known-bad input, which the positive cases below
establish. The scanner itself is AST-based (see module docstring in
timeout_audit_scan.py) -- an unparseable file raises SyntaxError, recorded by
scan_tree as a scan error, rather than falling back to a raw-text regex scan.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from execution.timeout_audit_scan import (  # noqa: E402
    is_test_path,
    scan_tree,
    scan_unbounded_wait_loops,
    scan_untimed_remote_calls,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# --- unbounded wait loops ------------------------------------------------

def test_unbounded_wait_loop_is_a_finding(tmp_path):
    p = _write(tmp_path, "bad_loop.py", "import time\n"
               "while not done():\n"
               "    time.sleep(15)\n")
    assert len(scan_unbounded_wait_loops(p)) == 1


def test_loop_with_deadline_is_not_a_finding(tmp_path):
    p = _write(tmp_path, "good_loop.py", "import time\n"
               "deadline = time.monotonic() + 60\n"
               "while not done():\n"
               "    if time.monotonic() > deadline:\n"
               "        break\n"
               "    time.sleep(15)\n")
    assert scan_unbounded_wait_loops(p) == []


def test_deadline_in_while_header_bounds_sleep_loop(tmp_path: Path) -> None:
    source = tmp_path / "bounded.py"
    source.write_text(
        "while time.monotonic() < deadline:\n    time.sleep(0.5)\n",
        encoding="utf-8",
    )
    assert scan_unbounded_wait_loops(source) == []


# --- untimed remote calls ------------------------------------------------

def test_untimed_remote_call_is_a_finding(tmp_path):
    p = _write(tmp_path, "bad_call.py",
               "import requests\n"
               "r = requests.get('https://example.com')\n")
    assert len(scan_untimed_remote_calls(p)) == 1


def test_untimed_call_is_flagged_by_ast(tmp_path: Path) -> None:
    source = tmp_path / "untimed.py"
    source.write_text("import subprocess\nsubprocess.run(['git', 'status'])\n", encoding="utf-8")
    assert scan_untimed_remote_calls(source) == [(2, "subprocess.run(['git', 'status'])")]


def test_timeout_beyond_six_lines_is_not_a_finding(tmp_path):
    """Regression: the fixed 6-line lookahead scored this as untimed."""
    p = _write(tmp_path, "long_call.py",
               "import requests\n"
               "r = requests.post(\n"
               "    url='https://example.com',\n"
               "    headers={'a': '1'},\n"
               "    json={\n"
               "        'k1': 1,\n"
               "        'k2': 2,\n"
               "        'k3': 3,\n"
               "    },\n"
               "    timeout=30,\n"
               ")\n")
    assert scan_untimed_remote_calls(p) == []


def test_mention_in_comment_or_docstring_is_not_a_finding(tmp_path):
    """Regression: prose describing an untimed call was reported as one."""
    p = _write(tmp_path, "prose.py",
               '"""Never write requests.get(url) with no timeout."""\n'
               "# e.g. subprocess.run(['git', 'status']) would hang\n"
               "VALID = True\n")
    assert scan_untimed_remote_calls(p) == []


def test_untimed_scan_raises_on_syntax_error(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        scan_untimed_remote_calls(source)


def test_syntax_error_file_is_reported_as_scan_error_not_silently_clean(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = scan_tree(tmp_path)
    assert "broken.py" in result["scan_errors"]
    assert result["error_count"] == 1
    assert result["untimed_remote_calls"] == {}


# --- runtime vs test classification --------------------------------------

def test_is_test_path_classification():
    assert is_test_path("Order Samurai/tests/test_x.py")
    assert is_test_path("tests/helpers.py")
    assert is_test_path("pkg/test_thing.py")
    assert not is_test_path("agentica_core/gateway.py")
    assert not is_test_path("execution/latest_results.py")


def test_scan_tree_splits_runtime_from_tests(tmp_path):
    """A `tests/` *directory* is skipped by the file-walk entirely (see
    `_iter_python_files`'s _SKIP_DIR_NAMES) -- test_count only ever fires for
    a stray `test_*.py` module that lives outside a `tests/` dir, which
    `is_test_path` still classifies by filename prefix."""
    _write(tmp_path, "runtime_mod.py",
           "import requests\nr = requests.get('https://example.com')\n")
    _write(tmp_path, "test_mod.py",
           "import subprocess\nsubprocess.run(['git', 'status'])\n")
    r = scan_tree(tmp_path)
    assert r["count"] == 2
    assert r["runtime_count"] == 1
    assert r["test_count"] == 1


def test_clean_tree_scores_zero(tmp_path):
    _write(tmp_path, "clean.py",
           "import requests\nr = requests.get('https://example.com', timeout=10)\n")
    r = scan_tree(tmp_path)
    assert r["count"] == 0
    assert r["runtime_count"] == 0
