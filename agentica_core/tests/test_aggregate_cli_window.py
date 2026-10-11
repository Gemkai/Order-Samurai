"""`python -m agentica_core.aggregate` must run ONE module copy of the aggregator.

Under `-m` the file executes as `__main__`. Reducers moved into `aggregate_*` modules
reach shared state through a function-local `from . import aggregate as _agg`; when the
`__main__` guard ran `main()` in the `__main__` copy, that import built a SECOND copy of
the module, so state `aggregate()` publishes with `global _ACTIVE_WINDOW_DAYS` never
reached the moved reducers. `_estimated_cost_savings` (moved) reads that window and is
suppressed for any window shorter than an ISO week, so a non-default window makes the
split visible in the written payload.

Isolated: the package is copied into a tmp monorepo (the golden fixture builder), HOME
and every root point inside it, and the payload lands in that tmp tree's Data/.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

FIXTURES = Path(__file__).resolve().parent / 'fixtures/aggregate_golden'
_spec = importlib.util.spec_from_file_location('aggregate_fixture_cli', FIXTURES / 'build_fixture.py')
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)

DEFAULT_WINDOW = 'window_days: int = 30,'


def _cli_payload(root, window_days):
    env = _builder.build(root, populated=True)
    source = root / 'Governance/agentica_core/aggregate.py'
    text = source.read_text()
    assert text.count(DEFAULT_WINDOW) == 1
    source.write_text(text.replace(DEFAULT_WINDOW, f'window_days: int = {window_days},'))
    env['PYTHONPATH'] = str(root / 'Governance')
    result = subprocess.run([sys.executable, '-s', '-m', 'agentica_core.aggregate'],
                            env=env, cwd=root, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert str(root / 'Data/wid_payload.json') in result.stdout
    return json.loads((root / 'Data/wid_payload.json').read_text())


def _cost_savings(payload):
    return next(env for groups in payload['pillars'].values() for metrics in groups.values()
                for name, env in metrics.items() if name == 'Estimated_Cost_Savings')


def test_cli_window_reaches_moved_reducer(tmp_path):
    payload = _cli_payload(tmp_path / 'short', 3)
    assert payload['window']['days'] == 3
    metric = _cost_savings(payload)
    assert metric['data_gap'] is True
    assert '3d telemetry window cannot cover ISO week' in metric['detail']


def test_cli_default_window_is_not_suppressed(tmp_path):
    payload = _cli_payload(tmp_path / 'default', 30)
    assert payload['window']['days'] == 30
    assert 'telemetry window cannot cover' not in _cost_savings(payload).get('detail', '')
