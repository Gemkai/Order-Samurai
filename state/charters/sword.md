# Charter - Sword (Security Integrity)
North-star: the harness security decisions are all captured as LIVE metrics.
Measurement: python execution/doctor.py
Acceptance: same 7 criteria as Bow.
Extra rule: never weaken a gate or verifier to make a metric easier to compute.

Highest-value candidates:
- SWORD-001: route guardrails.py + secret_scrubber_realtime.py hooks into
  autonomic_events -> Guardrail Blocks + Real-time Secret Scrubs LIVE - value 9, effort 3
- SWORD-002: scout score_security.py + security_gate_canary.py ->
  Security Score + Canary Health LIVE - value 7, effort 2

Baseline: Sword = 12 LIVE (2026-06-02)
