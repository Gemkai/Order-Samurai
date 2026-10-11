"""W2 payload characterization against b559e7d00 (aggregate blob c30cf1be6f91).

Each run imports a fresh fixture copy of the current package. Only fixture-root
paths are normalized. Never regenerate to make a refactor pass; recording is
restricted to the frozen baseline and requires two byte-equal independent runs.

Fixtures re-recorded once on c41dcfdd9 (same aggregate blob) for its intended
hero-summary change; see docs/plans/2026-10-07-order-samurai-hotspot-split/
W2CHAR-HERO-REFRESH.md.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

FIXTURES = Path(__file__).resolve().parent / 'fixtures/aggregate_golden'
_spec = importlib.util.spec_from_file_location('aggregate_fixture', FIXTURES / 'build_fixture.py')
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)


def _run(root, case, *args):
    env = _builder.build(root, populated=case != 'absent')
    return subprocess.run([sys.executable, '-s', '-P', str(root / 'bootstrap.py'),
                           str(root), case, *map(str, args)],
                          env=env, cwd=root, capture_output=True, timeout=60)


def _payload(root, case):
    result = _run(root, case)
    assert result.returncode == 0, result.stderr.decode()
    audit = json.loads((root / 'audit.json').read_text())
    assert Path(audit['package']).is_relative_to(root)
    assert Path(audit['aggregate']).is_relative_to(root)
    assert any(e[0] == 'subprocess.Popen' for e in audit['events'])
    return result.stdout, audit


def _metrics(payload):
    return {name: env for groups in payload['pillars'].values()
            for metrics in groups.values() for name, env in metrics.items()}


def _sentinels(payload, audit, root):
    events = audit['events']
    vault = root / 'Knowledge/vault/me'
    assert ['os.scandir', str(vault)] in events
    assert ['open', str(vault / 'index.md')] in events
    assert ['open', str(root / 'Governance/Order Samurai/state/tool_quality.json')] in events
    metrics = _metrics(payload)
    assert metrics['Knowledge_Staleness_Days']['val'] == '3.5'
    assert metrics['Index_Drift']['val'] == '1'
    assert metrics['Tool_Selection_Accuracy']['val'] == '92.0'
    idle = metrics['Mechanism_Liveness']
    assert {key: idle[key] for key in ('val', 'state', 'data_gap', 'tier')} == {
        'val': '—', 'state': 'idle_by_design', 'data_gap': True, 'tier': 'AUTO',
    }


@pytest.mark.parametrize('case', ['populated', 'absent', 'windows'])
def test_aggregate_golden(tmp_path, case):
    raw, audit = _payload(tmp_path / 'fixture', case)
    assert raw == (FIXTURES / f'{case}.json').read_bytes()
    payload = json.loads(raw)
    if case == 'absent':
        assert not any(payload['record_counts'].values())
        assert payload['operator_attention']['data_gap']
        assert sum(bool(e.get('data_gap')) for e in _metrics(payload).values()) >= 10
    else:
        last = payload[-1] if case == 'windows' else payload
        assert last['record_counts'] == {'antigravity': 16, 'claude': 16, 'codex': 16}
        assert last['remediation_efficacy']['attempted'] == 3
        assert sum(e['val'] not in (None, '—') for e in _metrics(last).values()) >= 65
        _sentinels(last, audit, tmp_path / 'fixture')
        if case == 'windows':
            assert audit['windows'] == [1, 7, 30]
            assert [p['window']['days'] for p in payload] == [1, 7, 30]
            assert len({p['window']['records'] for p in payload}) == 3


@pytest.mark.parametrize('case', ['populated', 'absent', 'windows'])
def test_independent_runs_are_byte_equal(tmp_path, case):
    first, first_audit = _payload(tmp_path / 'first', case)
    second, second_audit = _payload(tmp_path / 'second', case)
    assert first_audit['hash_seed'] == second_audit['hash_seed']
    assert first == second


@pytest.mark.parametrize('probe', ['open', 'listdir', 'scandir', 'glob', 'walk',
                                   'child', 'git-root', 'git-env'])
def test_containment_rejects_escape(tmp_path, probe):
    result = _run(tmp_path / 'fixture', f'probe:{probe}', Path(__file__).resolve())
    assert result.returncode != 0
    assert b'ContainmentViolation' in result.stderr, result.stderr.decode()


def _regenerate():
    source = FIXTURES.parents[2] / 'aggregate.py'
    import hashlib
    data = source.read_bytes()
    digest = hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()
    assert digest == 'c30cf1be6f910c529ad9892fe58a3b87d90ad0a7', digest
    for case in ('populated', 'absent', 'windows'):
        with tempfile.TemporaryDirectory(prefix='w2char-') as temp:
            root = Path(temp).resolve()
            first, audit = _payload(root / 'first', case)
            second, audit2 = _payload(root / 'second', case)
            assert first == second
            assert audit['hash_seed'] == audit2['hash_seed']
            if case != 'absent':
                payload = json.loads(first)
                _sentinels(payload[-1] if case == 'windows' else payload, audit, root / 'first')
        (FIXTURES / f'{case}.json').write_bytes(first)
        print(f'{case}: two independent runs byte-equal ({len(first)} bytes)')


if __name__ == '__main__':
    assert sys.argv[1:] == ['--regenerate']
    _regenerate()
