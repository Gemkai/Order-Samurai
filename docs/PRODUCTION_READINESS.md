# Order Samurai — Production Readiness

Last updated 2026-08-17. Current release **v1.0.2**. This document is the standing
answer to "can this be promoted?" — update it, don't fork it.

## Verdict

| Surface | Status | Blocking on |
|---|---|---|
| Free Core (public) | **Ready** | None (1,600+ tests green, hooks verified, packaging clean) |
| Founding / Early Access Pro | **Ready (Early Access)** | Live customer purchase feedback |
| Broad paid GA | **Blocked** | End-to-end live purchase-to-refund verification with merchant |

Free Core does what it claims as of v1.0.2: the guard registers where Claude Code
loads it (`~/.claude/settings.json`), the installer verifies a sha256 checksum before extracting,
and the public copy describes the real implementation truthfully with zero private paths.

## What v1.0.0 got wrong, and why it shipped

`samurai install` wrote its hooks to `~/.claude/hooks/settings.json` as
`{"name","command","async"}`. Claude Code loads `~/.claude/settings.json` and parses
`{"matcher","hooks":[{"type":"command",...}]}` — wrong file **and** wrong schema, so
the prompt-injection guard and secret scrubber never fired. `samurai doctor` reported
5/5 PASS the entire time, because it validated Order Samurai's own settings file.

It shipped because `tests/test_samurai_installer.py` asserted the broken path and
schema. The test encoded the defect as the contract, and there was no CI. Two
source-level reviews missed it; it only surfaced by running the shipped artifact.

**The rule this bought:** a control is not verified until something exercises it
end to end, from the artifact a customer actually downloads. Self-reported health
checks are not evidence.

## Guards now in place

| Guard | Repo | Catches |
|---|---|---|
| `tests/test_hook_wiring.py` | pack | guard registered to the wrong file/schema; doctor false-green; uninstall clobbering user hooks |
| CI `install-smoke` job | pack | the above, against a real install in a clean HOME, every push |
| `tests/test_license_lifecycle.py` | pack | invalid/expired/revoked keys not failing closed; network error treated as valid; license data leaking to logs |
| `tests/test_installer_upgrade.py` | pack | upgrade not backing up; non-idempotent install; paths with spaces |
| CI `packaging` job | pack | internal strategy docs / `__pycache__` / `.ps1` entering the public zip |
| `tests/test_public_claims.py` | landing | `.dmg` and menu-bar claims, "1-click", stale test counts, privacy absolutes, dead local links, **zip/sidecar drift** |
| `tests/test_demo_integrity.py` | landing | real telemetry or secrets in the demo payload; missing synthetic labelling |
| `demo/validate_payload.py` | landing | placeholder values, incoherent totals, active-leak framing, implausible magnitudes, header/prose disagreement |
| `tests/test_version_sync.py` | landing | site version drifting from the shipped zip |

All were mutation-tested: each was confirmed to fail when the defect it guards is
reintroduced. A suite that passes on a clean tree proves nothing on its own.

## Known-good invariants

- **The served zip and its `.sha256` sidecar must change in the same commit.**
  `install.sh` aborts on mismatch, so drift breaks every install loudly. Guarded.
- **The demo payload is synthetic and version-controlled.** Real maintainer telemetry
  was public for ~19 days before 2026-08-09. Never regenerate it from live data.
- **Cloud review is opt-in.** Keys in the environment and a `claude` binary on PATH
  do not authorise transmission; `--enable-cloud-review` or
  `SAMURAI_ENABLE_CLOUD_REVIEW=1` does, and payloads are redacted first.

## Open blockers

1. **No reachable contact address.** `ordersamurai.dev` has no DNS records at all;
   `.ai`, `.com` and `agentica.biz` resolve but publish no MX, and `agentica.biz`
   has port 25 closed, so the RFC 5321 A-record fallback also fails. Every address
   in `SECURITY.md`, `TERMS.md`, `EULA.md` and on the live site bounces. A paid tier
   with a 14-day refund guarantee and a 24h security-ack SLA has no channel.
   *Blocks: Free Core promotion, and all paid tiers.*
2. **Paid lifecycle never exercised end to end.** The harness mocks the provider
   boundary; no real purchase → delivery → activation → refund cycle has run.
   *Blocks: all paid tiers.*
3. **Issue #2** — an external evaluator is holding security findings across the
   reflex engine, dashboard API and installer, one concerning "whether a shipped
   security control engages at all." Private vulnerability reporting is now enabled;
   the report has not yet been received or triaged.
4. **No signing or attestation.** `docs/RELEASE.md` has the hook point; no identity
   exists. Releases are checksum-verified but unsigned.
5. **SLA is unearned.** `SECURITY.md` promises 24h acknowledgement / 72h triage.
   Either staff it or soften it.

## Deferred (not blocking)

- Dashboard bundle is ~600KB; no code splitting or performance budget.
- No uptime/synthetic monitoring (needs an external account).
- No issue templates or `.github/` community files on the public repo.
- Host-spawn audit fallback (`REPO_AUDIT_CONTAINER=false`) inherits unfiltered env —
  inert while the consent gate holds, but an explicit strip would be better.
- `CONTAINER_ENV_NAMES` / `isCloudCredEnvName` have no dedicated unit test.

## Promotion checklist

- [ ] A published contact address accepts mail (verify: send to it and receive)
- [ ] `SECURITY.md` SLA matches what you will actually do
- [ ] Issue #2 report received, triaged, and answered
- [ ] One real purchase → delivery → activation → refund, on a clean machine
- [ ] CI green on both repos for a full week without manual intervention
- [ ] `samurai install` verified on a machine that is not the author's
