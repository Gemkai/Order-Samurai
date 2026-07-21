# agentica_core

Platform-neutral Governance kernel for Agentica OS.

## Core Contracts
- `adapter.py`: resolves each platform into four slots: runtime root, telemetry source, verifier
  provider, and surface matrix.
- `telemetry.py`: canonical `agentica.1` telemetry schema plus metric-envelope honesty checks.
- `verifiers.py` and `doctor.py`: normalize and run platform checks without one failing verifier
  blinding the whole doctor.
- `aggregate.py`: builds the cross-platform WID payload consumed by dashboards and reports.
- `state_report.py`: writes the current authoritative posture to `Data/reports/current-state.md`
  and root `STATE.md`.

## Platforms
Registered in `platforms.json`:

- `claude`: Order Samurai verifier provider.
- `antigravity`: Jarvis/Core verifier provider.
- `codex`: tracked Codex surface matrix plus telemetry/runtime verifier provider.

Run:

```powershell
python -m agentica_core.doctor claude
python -m agentica_core.doctor antigravity
python -m agentica_core.doctor codex
```

Exit code `1` means at least one FAIL. Exit code `2` means the platform could not be resolved.

## Reports And Dashboard

```powershell
python -m agentica_core.aggregate       # Data/wid_payload.json
python -m agentica_core.state_report   # Data/reports/current-state.md + STATE.md
python -m agentica_core.weekly_report  # Data/reports/<week>__<platform>.md
python refresh_dashboard.py --snapshot # payload + history + reports + dashboard public copy
```

## Tier Honesty
Every metric envelope declares a tier: `AUTO`, `DERIVED`, `SIMULATED`, or `SKILL`. Current dashboard
cards are measured rather than simulated; future placeholders must stay visibly marked until wired.

## Tests

Install dev dependencies once:

```powershell
cd "~/Agentica-OS"
python -m pip install -r requirements-dev.txt
```

Run the kernel tests:

```powershell
cd "~/Agentica-OS\Governance"
python -m pytest agentica_core/tests -q
```

Run the combined gate:

```powershell
.\verify.ps1 -Fast
```
