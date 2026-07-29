"""emit_event must resolve the real Governance dir so it imports the telemetry kernel.

emit_event prepends `_GOVERNANCE` to sys.path and then does
`from agentica_core.telemetry import ...`. The path was computed with four
`.parent` hops plus a stale `"Agentica OS"/"Governance"` suffix (pre-relocation
Desktop layout), landing on a directory that does not exist. The import then
silently failed and the fallback wrote reflex-engine events to a dead
`~/Desktop/Agentica OS/...` path that no consumer reads.

Since the script now lives at <repo>/Governance/Order Samurai/bin/, the real
Governance dir is exactly three parents up.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bin import emit_event  # type: ignore[import-not-found]


def test_resolved_governance_dir_contains_the_telemetry_kernel_it_imports():
    assert (emit_event._GOVERNANCE / "agentica_core" / "telemetry.py").is_file()
