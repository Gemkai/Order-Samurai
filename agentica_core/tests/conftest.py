"""Put the Governance dir on sys.path so `import agentica_core` resolves when pytest
runs from inside the package's tests dir (the repo path contains a space)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
