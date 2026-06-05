# Charter - Bow (Operational Status)
North-star: every runtime-health signal is a real LIVE metric in aggregate.py.
Measurement: python execution/doctor.py
Acceptance (all must hold before commit):
1. VALIDATE_CMD exits clean
2. Targeted metric reads from its declared real source
3. Bow LIVE count >= 15 (baseline 2026-06-02)
4. 0 metrics shown LIVE without a backing source
5. METRICS.md status + aggregator REGISTRY updated in lockstep
6. New capability shipped as a manually-runnable skill, not a daemon
7. directives/ untouched

Highest-value candidates:
- BOW-001: autonomic_events.jsonl emitter - unlocks Config Drift, MTTH, Zombie/Daemon,
  Hook Failure together - value 9, effort 4
- BOW-002: per-tool ok:bool in tool_latencies -> Tool Failure Rate LIVE - value 7, effort 2
