# Agentica OS Governance Dashboard

The rich dashboard is a Vite + React + TypeScript SPA with the Jarvis-inspired command-center
feel, recolored for the Agentica pillars: Bow, Sword, Brush, and Arts. It consumes the
renderer-neutral `Data/wid_payload.json`. The zero-dependency static `render.py` dashboard remains
as a fallback.

## Run It
```bash
cd "Agentica OS/Governance/dashboard-ui"
npm install
npm run sync
npm run dev
```

Production:
```bash
npm run build
npm run preview
```

## Data Flow
- `npm run sync` runs `python -m agentica_core.aggregate` from `Governance/` and copies
  `Data/wid_payload.json` into `public/`.
- `python ../refresh_dashboard.py --snapshot` refreshes the payload, appends metric history, and
  regenerates reports.
- The app fetches `/wid_payload.json` and report markdown from `/reports/`.

## Verification Status
Built, type-checked, linted, and visually smoke-tested with Playwright screenshots. If a chart looks
off, the bklit/Visx components are in `src/components/charts/`.

## Notes
- Trend sparklines populate from `Data/telemetry/metrics_history.jsonl`; use `--snapshot`
  occasionally to append a stable baseline.
- Dashboard screenshots named `_*.png` are QA artifacts and are ignored by the root repository.
