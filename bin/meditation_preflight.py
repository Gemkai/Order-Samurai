"""Reject paused or unbudgeted meditation before any agent is launched."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
_GOVERNANCE = _ROOT if (_ROOT / "agentica_core").is_dir() else _ROOT.parent
sys.path.insert(0, str(_GOVERNANCE))
from agentica_core.bushido_engine import _over_daily_budget  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True, type=Path)
    args = parser.parse_args()
    stop = args.state_dir / "MEDITATION_STOP"
    try:
        stop.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        print("Meditation pause status unavailable; refusing to run.", file=sys.stderr)
        return 2
    else:
        print("Legacy meditation paused (MEDITATION_STOP).", file=sys.stderr)
        return 2
    if _over_daily_budget(args.state_dir.parent, ledger_path=args.state_dir / "budget_ledger.json"):
        print("Meditation blocked: current UTC-day budget unavailable, invalid or exhausted. Refresh through the budget owner before retrying.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
