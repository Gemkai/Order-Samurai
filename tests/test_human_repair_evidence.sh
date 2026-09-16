#!/usr/bin/env bash
set -euo pipefail

npm --prefix Governance/api run test:run -- src/reflex-engine-manual-evidence.test.ts
<REPO_ROOT>/.venv/bin/python -m pytest \
  Governance/agentica_core/tests/test_remediation.py::test_efficacy_keeps_dashboard_repair_as_human_evidence_only -q
npm --prefix Governance/dashboard-ui run test:run -- src/components/RemediationPanel.human-evidence.test.ts
