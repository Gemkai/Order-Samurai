"""Pre-import clock/probe shim and fail-closed audit fence for W2 characterization."""
import datetime as dt
import glob
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import time

ROOT = Path(sys.argv[1]).resolve()
CASE = sys.argv[2]
NOW = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
LIBRARIES = tuple({Path(sysconfig.get_path(name)).resolve()
                   for name in ('stdlib', 'platstdlib', 'purelib', 'platlib')})
sys.path[:] = [str(ROOT / 'Governance')] + [
    p for p in sys.path if p and any(Path(p).resolve().is_relative_to(lib) for lib in LIBRARIES)]
sys.dont_write_bytecode = True
assert sys.flags.ignore_environment == 0 and sys.flags.no_user_site and sys.flags.safe_path
assert os.environ['PYTHONHASHSEED'] == '0'
os.chdir(ROOT)
os.environ.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                  GIT_CEILING_DIRECTORIES=str(ROOT.parent))
EVENTS = []
VIOLATIONS = []


class ContainmentViolation(BaseException):
    """Production's best-effort `except Exception` must never hide an escape."""


def reject(message):
    VIOLATIONS.append(message)
    raise ContainmentViolation(message)


def check_path(path, event, base=None):
    if isinstance(path, int):
        reject(f'{event}: untraceable directory fd {path}')
    path = Path(os.fsdecode(path))
    resolved = ((base or Path.cwd()) / path).resolve()
    if not (resolved.is_relative_to(ROOT) or any(resolved.is_relative_to(lib) for lib in LIBRARIES)):
        reject(f'{event}: read escaped fixture: {resolved}')
    return resolved


def audit(event, args):
    if event == 'subprocess.Popen':
        executable, argv, cwd, env = args
        if not isinstance(argv, (tuple, list)) or argv[0] != 'git' or executable != 'git':
            reject(f'{event}: forbidden child {argv!r}')
        location = Path(cwd or os.getcwd()).resolve()
        if not location.is_relative_to(ROOT):
            reject(f'{event}: cwd escaped fixture: {location}')
        for i, arg in enumerate(argv):
            if arg == '-C':
                location = (location / argv[i + 1]).resolve()
                if not location.is_relative_to(ROOT):
                    reject(f'{event}: -C escaped fixture: {location}')
        child_env = os.environ if env is None else env
        for name, value in {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                            'GIT_CEILING_DIRECTORIES': str(ROOT.parent)}.items():
            if child_env.get(name) != value:
                reject(f'{event}: missing git fence {name}')
        EVENTS.append([event, list(argv)])
    elif event in ('open', 'os.listdir', 'os.scandir', 'os.walk', 'glob.glob',
                   'glob.glob/2', 'pathlib.Path.glob', 'pathlib.Path.rglob'):
        path = args[0]
        if event == 'open' and isinstance(path, int):
            return  # Popen pipes are anonymous fds, not filesystem reads.
        if event == 'glob.glob/2':
            if args[3] is not None:
                reject('glob.glob/2: directory fds are not allowed')
            base = check_path(args[2] or '.', event)
        else:
            base = None
        resolved = check_path(path if path is not None else '.', event, base)
        EVENTS.append([event, str(resolved)])
    elif event in ('socket.connect', 'socket.getaddrinfo', 'os.system', 'os.posix_spawn'):
        reject(f'{event}: forbidden external access')


sys.addaudithook(audit)


class FrozenDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return NOW.replace(tzinfo=None)


class FrozenDate(dt.date):
    @classmethod
    def today(cls):
        return NOW.date()


dt.datetime = FrozenDateTime
dt.date = FrozenDate
time.time = lambda: NOW.timestamp()
time.monotonic = lambda: 1000.0


class ShimLoader:
    def __init__(self, loader):
        self.loader = loader

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        if module.__name__ == 'agentica_core.scouts':
            module.agent_process_count = lambda: 4 if CASE != 'absent' else None
        elif module.__name__ == 'agentica_core.verify_secrets':
            module.run_checks = lambda: []
        elif module.__name__ == 'agentica_core.display_evidence':
            module.uuid4 = lambda: '00000000-0000-4000-8000-000000000041'


class ShimFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('agentica_core.scouts', 'agentica_core.verify_secrets',
                        'agentica_core.display_evidence'):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            spec.loader = ShimLoader(spec.loader)
            return spec


sys.meta_path.insert(0, ShimFinder())
# The optional private providers are absent in the portable product fixture.
for name in ('execution.verify_falsifiability', 'execution.timeout_audit_scan',
             'execution.skill_routing_adherence', 'execution.escalation_sla',
             'principle_audit', 'agentica_core.licensing'):
    sys.modules[name] = None

# Probes exercise the installed hook, including rejection of real source *code*.
if CASE.startswith('probe:'):
    probe, outside = CASE.split(':', 1)[1], Path(sys.argv[3])
    if probe == 'open':
        outside.read_text()
    elif probe == 'listdir':
        os.listdir(outside.parent)
    elif probe == 'scandir':
        list(os.scandir(outside.parent))
    elif probe == 'glob':
        glob.glob(str(outside.parent / '*.py'))
    elif probe == 'walk':
        list(os.walk(outside.parent))
    elif probe == 'child':
        subprocess.run([sys.executable, '-c', 'pass'], check=True)
    elif probe == 'git-root':
        subprocess.run(['git', '-C', str(outside.parent), 'status'], check=True)
    elif probe == 'git-env':
        subprocess.run(['git', '-C', str(ROOT), 'status'], env={}, check=True)
    raise AssertionError('containment probe unexpectedly succeeded')

import agentica_core
from agentica_core import aggregate as agg

assert Path(agentica_core.__file__).resolve().is_relative_to(ROOT)
assert Path(agg.__file__).resolve().is_relative_to(ROOT)
# Copying the code before import redirects even function-local __file__ roots.
# Assert all aggregate path globals (including future additions), not only known ones.
def assert_paths(value):
    if isinstance(value, Path):
        assert value.resolve().is_relative_to(ROOT), value
    elif isinstance(value, dict):
        for item in value.values():
            assert_paths(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_paths(item)


for name, value in vars(agg).items():
    if name.startswith('_'):
        assert_paths(value)
assert agg._VAULT_HEALTH_SCRIPT == ROOT / 'Knowledge/vault/_scripts/vault_health.py'
assert agg._AGENTICA_REPO_ROOT == ROOT
assert all(path.is_relative_to(ROOT) for path in agg._KILL_CHAIN_EXTRA_ROOTS)
payloads = []
windows = [1, 7, 30] if CASE == 'windows' else [30]
for days in windows:
    payload = agg.aggregate(timestamp=NOW.isoformat(), window_days=days, write_history=False)
    assert agg._ACTIVE_WINDOW_DAYS == days
    payloads.append(payload)
if CASE != 'absent':
    # The payload formats this raw null as an em dash.
    assert agg._mechanism_liveness([{'timestamp': NOW.isoformat()}])['val'] is None
assert not VIOLATIONS, VIOLATIONS
result = payloads if CASE == 'windows' else payloads[0]
(ROOT / 'audit.json').write_text(json.dumps({'events': EVENTS, 'hash_seed': hash('abc'),
    'package': agentica_core.__file__, 'aggregate': agg.__file__, 'windows': windows}))
print(json.dumps(result, sort_keys=True, indent=2).replace(str(ROOT), '<FIXTURE>'))
