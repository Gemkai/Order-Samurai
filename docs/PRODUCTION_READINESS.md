# Order Samurai — Production Readiness

Updated 2026-10-02. Prepared patch version **2.0.1**, based on public source
`f2c424690bfbe6b72076cde9238067ada7db69c4`. This is the standing promotion record,
not a claim that current source has reached every customer-facing channel.

## Verdict

| Surface | Status | Remaining gate |
|---|---|---|
| Free Core | Controlled release | Publish and verify the corrected customer artifact and demo |
| Pro | Early access | Same release gates; real purchase/delivery/activation evidence outstanding |
| Broad paid GA | Not verified | Real paid lifecycle, reachable support, independent clean-machine install |

## Evidence and limits

- Public main includes the Gumroad activation/license/deactivation CLI and retires
  broad autonomous repair. Both tiers require explicit approval before patch application.
- Focused source licensing/hook/installer/public-claim checks were 44 passed,
  3 skipped on October 2. Skipped site-installer tests had a stale local site path,
  not a passing upgrade result.
- Main CI run [36868606296](https://github.com/Gemkai/Order-Samurai/actions/runs/36868606296)
  passed Python 3.11/3.12, install smoke, shellcheck and packaging. Python 3.13
  stopped during dependency installation after a runner shutdown; this is not a
  complete passing matrix.
- The public source ZIP and customer site's ZIP differed despite both declaring
  2.0.0. Website packaging lives in a separate repository. Checksums establish
  consistency, not publisher identity; artifacts remain unsigned.
- Demo data is synthetic, not a live fleet or customer-result benchmark. Never
  replace it with live telemetry. Release evidence must identify the exact source
  revision, package checksum, and checks actually executed.
- Licensing tests mock Gumroad. They do not establish a real purchase → delivery →
  activation → refund cycle. Activation validates online; normal entitlement reads
  are local. A refund does not automatically revoke an activated offline entitlement.
- Canonical contact addresses are under `ordersamurai.ai`; consistent spelling is
  not proof a mailbox accepts mail. Verify a sent message was received. Paid
  customers have seller contact through their receipt; security reports can use
  GitHub private vulnerability reporting.

## Existing safeguards

Hook-wiring and install smoke tests check Claude Code's actual settings schema,
preserve unrelated hooks, and reject false-green doctor results. Licensing fails
closed on malformed/inactive/refunded local records. Public-site tests check
ZIP/checksum/version consistency, links, and synthetic-data leakage. Demo validation
must reconcile rollups, metric references and narrative facts—not just accept JSON.
Cloud review requires explicit opt-in and redaction.

## Promotion checklist

- [ ] Product and site changes reviewed and landed at confirmed heads
- [ ] Same final ZIP and checksum served by site, demo and GitHub release
- [ ] Served archive downloaded after deployment and tested in a clean environment
- [ ] Complete CI matrix passes at the final release revision
- [ ] Support/security messages sent and receipt confirmed
- [ ] Existing external security report/Issue #2 disposition checked and recorded
- [ ] Real customer key delivered and activated; refund behavior observed and documented
- [ ] Install verified on a machine other than the maintainer's

Do not mark these complete from source inspection or mocked tests. Publication,
mailbox receipt, real payment and actual customer installations are separate outcomes.
Follow [RELEASE.md](RELEASE.md) for publication and rollback.
