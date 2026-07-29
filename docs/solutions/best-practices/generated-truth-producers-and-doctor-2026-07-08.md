---
title: "Generated-truth producers + the single doctor flow: answer existence from disk, never hand-curate"
date: "2026-07-08"
category: "docs/solutions/best-practices"
module: "sync_inventory sync_capability_manifest doctor"
problem_type: "architecture"
component: "tooling"
symptoms:
  - "Hand-maintained inventories/manifests drift from on-disk reality and silently lie about what exists"
  - "verify_registry_truth FAILs when a declared surface path no longer resolves"
  - "Layer checks were spectators (logged WARN) rather than gates (blocked drift)"
root_cause: "design"
resolution_type: "architecture"
severity: "medium"
related_components:
  - "execution/verify_registry_truth.py"
  - "config/hub_capability_manifest.json"
  - "artifacts/inventory.json"
  - "config/root_hygiene_policy.json"
tags: [anti-drift, generated-truth, doctor, verifier, surface-governance, order-samurai]
---

# Generated-truth producers + the single doctor flow

Order Samurai's anti-drift stance is that **existence questions are answered by generation, not by hand**. A registry a human maintains drifts the moment the disk changes; a registry generated *from* the disk cannot. Three modules implement this.

## The producers — generate truth from disk

- **`sync_inventory.py`** answers *"what top-level surfaces exist?"* by classifying every root entry against `config/root_hygiene_policy.json` and writing `artifacts/inventory.json`. Output is deterministic (sorted, no wall-clock) so re-running yields no spurious diff. It satisfies the `generated-truth-over-manual-inventory` anti-drift rule.
- **`sync_capability_manifest.py`** answers *"what surfaces are discoverable?"* by emitting `config/hub_capability_manifest.json` — a path-identified, deterministically ordered list of live/support surfaces generated from disk. Archive, exploratory, dependency, state, and metadata roots are excluded: a manifest must only advertise surfaces that are real and runtime-approved.

Because both are generated from disk, every `surfaces[].path` they declare resolves — which is exactly what `execution/verify_registry_truth.py` checks. A hand-curated manifest is where the "declared but missing" FAILs come from; a generated one structurally can't produce them.

## The verifier of record — `doctor.py`

`doctor.py` is the **single coherent flow** that runs the layer verifiers (path authority, runtime contract, archive boundaries, surface governance, root hygiene, …) and rolls them into one OK/WARN/FAIL verdict per runtime. The post-resurrection hardening turned these checks from *spectators* (that merely logged a WARN) into *gates* (that actually block drift), so a failing invariant now has teeth instead of scrolling past in a log.

## The rule

When you need to answer "what exists / what is approved", **write a generator that reads disk and emits a deterministic artifact**, then point a verifier at it — never hand-maintain the list. Generated truth answers existence; hand-maintained files answer *policy and intent* (see `config/hub_surface_matrix.json`, which is the deliberately hand-authored role/owner/discoverability layer). Keeping those two jobs separate is the whole `truth_separation` scorecard category.
