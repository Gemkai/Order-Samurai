# Ronin decisions

Ronin ON lets routine, classified work proceed within existing permission, budget and release rules. It is an approval policy, not a scheduler. The deprecated meditation cycle button has been removed; historical output remains available.

Factory technical failures become repair feedback. Existing workers retry the same ticket within their attempt and token limits, using scanned feedback and the existing isolated worktree. Failed checks never count as passing. When repair attempts run out, the task stops with a recorded technical reason instead of asking the owner to approve a broken result. Turning Ronin OFF stops prepared automatic repairs from being dispatched.

## What the owner still decides

- Access and security: connect a new service, broaden credentials, change protected permissions or security controls.
- Spending: raise an agreed budget or authorize an additional paid commitment. Approving a request does not itself reset a budget or bypass a hard stop.
- Business commitments: change a customer-facing promise, price or contractual commitment, where the connected workflow supports that action.
- Strategy: choose a materially different product direction or approve work outside the agreed scope.
- Irreversible consequences: an action with material permanent effects; broad irreversible actions remain hard-stopped.
- Unclassified authority: classify an unknown skill or an unclassified system-wide action before it runs automatically.
- Existing release gates: protected paths, test configuration, security/quarantine, repository origin and explicit release-phase restrictions still require their existing decision or remediation.

A useful request states the outcome, cost or impact, the system's recommendation and the consequence of declining in plain language. It should ask the owner to choose a business outcome, not to interpret a failing test log.

## Enforcement and limits

The engine supports `routine`, `security`, `privacy`, `spending`, `business`, `strategic` and `unknown` decision categories. An unknown category requires owner review. The CLI can add an owner category with `--decision-category`; it cannot reduce a skill's declared category. Only trusted workflow code and the skill policy table should classify work. This is an explicit policy mechanism, not an AI classifier of arbitrary instructions or a new customer-action integration.

Owner approvals are tied to the command, scope, category and proposal context. A changed proposal needs a new decision. Owner-category approvals stay available for a context-preserving trigger; the legacy command-only immediate-dispatch transport is not used for them. Historical queue entries are not rewritten or automatically approved by this change.

The existing factory release policy still applies. Ronin does not automatically merge a release merely because tests pass when that release phase requires a human decision.
