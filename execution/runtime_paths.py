from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKLOG_DIR = REPO_ROOT / "backlog"
CONFIG_DIR = REPO_ROOT / "config"
EXECUTION_DIR = REPO_ROOT / "execution"
REPORTS_DIR = REPO_ROOT / "reports"
TESTS_DIR = REPO_ROOT / "tests"
TMP_DIR = REPO_ROOT / ".tmp"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

ROOT_HYGIENE_POLICY_PATH = CONFIG_DIR / "root_hygiene_policy.json"
PROMOTION_POLICY_PATH = CONFIG_DIR / "promotion_policy.json"
ANTI_SPRAWL_POLICY_PATH = CONFIG_DIR / "anti_sprawl_policy.json"
ANTI_DRIFT_POLICY_PATH = CONFIG_DIR / "anti_drift_policy.json"
