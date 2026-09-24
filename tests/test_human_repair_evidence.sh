#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
npm --prefix api run test:run -- src/autonomous-repair-retirement.acceptance.test.ts src/manual-authority-boundary.acceptance.test.ts
"${PYTHON:-python3}" -m pytest \
  agentica_core/tests/test_remediation.py::test_efficacy_keeps_dashboard_repair_as_human_evidence_only -q
npm --prefix dashboard-ui run test:run -- src/components/RemediationPanel.retirement.acceptance.test.ts
