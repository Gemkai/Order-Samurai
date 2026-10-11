"""Build a portable monorepo fixture from the current package and fixed inputs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
MTIME = NOW.timestamp() - 3.5 * 86400


def environment(root):
    return {
        'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'TZ': 'UTC',
        'AGENTICA_COST_SAVINGS_V2': 'true', 'AGENTICA_METRIC_WEEK_FROM_RECORDS': 'true',
        'PYTHONHASHSEED': '0', 'HOME': str(root / 'home'),
        'USERPROFILE': str(root / 'home'), 'ORDER_SAMURAI_ROOT': str(root / 'Governance/Order Samurai'),
        'AGENTICA_REPO_ROOT': str(root), 'AGENTICA_PROJECTS_ROOT': str(root / 'projects'),
    }


def git_environment(root):
    return dict(environment(root), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                GIT_CEILING_DIRECTORIES=str(root.parent))


def build(root, populated):
    root.mkdir(parents=True)
    package = HERE.parents[2]
    dest = root / 'Governance/agentica_core'
    # Copy only code, not live package data, caches or the test fixture itself.
    for path in sorted(package.rglob('*.py')):
        rel = path.relative_to(package)
        if 'tests' not in rel.parts and '__pycache__' not in rel.parts:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    shutil.copytree(package.parent / 'schema', root / 'Governance/schema')
    # Presentation policy is a runtime input consumed by insights, alongside the
    # copied package and schema. Keep the portable fixture self-contained.
    shutil.copyfile(package.parent / 'metric_disposition.json',
                    root / 'Governance/metric_disposition.json')
    inputs = json.loads((HERE / 'inputs.json').read_text())
    registry = inputs['platforms']
    if populated:
        for name, content in inputs['files'].items():
            if name in ('scouts.json', 'verifiers.json'):
                continue
            target = root / name.removeprefix('repo/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        rows = inputs['files']['verifiers.json']
        (root / 'Governance/fixture_verifiers.py').write_text(
            'import json\nROWS = json.loads(' + repr(rows) + ')\n' +
            '\n'.join(f'def {p}(): return [lambda: ROWS[{p!r}]]' for p in registry['platforms']))
        for name, spec in registry['platforms'].items():
            spec['verifiers'] = f'fixture_verifiers:{name}'
        config = root / 'Governance/Order Samurai/config'
        for name in ('architecture_scorecard', 'claude_architecture_scorecard',
                     'codex_architecture_scorecard', 'gemini_architecture_scorecard'):
            shutil.copyfile(config / 'scorecard.json', config / f'{name}.json')
        vault = root / 'Knowledge/vault/me'
        vault.mkdir(parents=True)
        (vault / 'w2char-sentinel.md').write_text('# W2CHAR vault sentinel\n')
        (vault / 'index.md').write_text('[sentinel](w2char-sentinel.md)\n[missing](missing.md)\n')
        # Real git history, built with plumbing; no checkout or host repository mutation.
        env = git_environment(root)
        subprocess.run(['git', 'init', '--bare', str(root / '.git')], env=env,
                       check=True, capture_output=True)
        subprocess.run(['git', '--git-dir', str(root / '.git'), 'config', 'core.bare', 'false'],
                       env=env, check=True, capture_output=True)
        stream = ''
        for branch, mark, date, parent, subject in (
            ('work', 1, '2026-10-01T12:00:00+00:00', None, 'fixture start'),
            ('work', 2, '2026-10-04T12:00:00+00:00', 1, 'refactor: fixture cleanup'),
            ('fixture-stale', 3, '2026-10-02T12:00:00+00:00', 1, 'fixture side branch'),
        ):
            date = f'{int(datetime.fromisoformat(date).timestamp())} +0000'
            stream += (f'commit refs/heads/{branch}\nmark :{mark}\n'
                       f'author Fixture <fixture@example.invalid> {date}\n'
                       f'committer Fixture <fixture@example.invalid> {date}\n'
                       f'data {len(subject)}\n{subject}\n')
            if parent:
                stream += f'from :{parent}\n'
            stream += '\n'
        stream += 'reset refs/heads/fixture-merged\nfrom :1\n\ndone\n'
        result = subprocess.run(['git', '-C', str(root), 'fast-import', '--date-format=raw', '--quiet'],
                       input=stream, text=True, env=env, capture_output=True)
        assert result.returncode == 0, result.stderr
        (root / '.git/HEAD').write_text('ref: refs/heads/work\n')
    (root / 'home').mkdir(exist_ok=True)
    (dest / 'platforms.json').write_text(json.dumps(registry))
    shutil.copyfile(HERE / 'bootstrap.py', root / 'bootstrap.py')
    for path in sorted(root.rglob('*'), reverse=True):
        os.utime(path, (MTIME, MTIME))
    os.utime(root, (MTIME, MTIME))
    return environment(root)
