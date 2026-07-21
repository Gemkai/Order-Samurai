# Security Policy & Governance Posture

Order Samurai is designed to secure agentic coding workflows. We take the security of our governance layer and the security of your agent fleet seriously.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x: |

## Reporting a Vulnerability

If you discover a potential security flaw, prompt-injection bypass, secret scrubber leak, or security gate bypass in Order Samurai:

1. **Email us directly** at `security@ordersamurai.dev` (or notify maintainers directly).
2. **Include details**: Steps to reproduce, agent runtime used (Claude Code, etc.), tool inputs/outputs, and sample logs if safe to share.
3. **Response timeline**: We acknowledge receipt within 24 hours and aim to provide a triage decision and patch within 72 hours.
4. **Public Disclosure**: Please allow us to patch the issue before making public disclosures.

## Security Guarantees & Architecture

- **Data Boundary**: Zero external cloud telemetry. All metric aggregation, secret scrubbing, and kill-chain analysis happen on your local filesystem (`~/.samurai/`).
- **Fail-Closed Gate Enforcement**: Security gates block on failure (`BUSHIDO_FAIL_OPEN=false`).
- **Secret Scrubbing**: Real-time PostToolUse and PreToolUse hooks inspect stdout, file edits, and agent outputs to prevent credential/IP leakages (ATT&CK Chain 14).
- **Prompt Injection Defense**: Dual-layer pattern matching + local model scoring intercepts indirect prompt injections (ATT&CK Chain 13).
