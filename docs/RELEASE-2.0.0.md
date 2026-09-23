# Order Samurai 2.0.0 — repair retirement

Broad automatic LLM repair and patch auto-application are retired. Score regressions no longer launch repair cycles. Legacy REFLEX_AUTO_APPLY cannot enable patch application. Operator-requested work, diagnostic scans, bounded deterministic maintenance, and historical records remain available. Repair history is collapsed by default and labeled historical.

The installer again registers hooks in the actual Claude settings file. Reinstallation preserves other hooks; uninstall preserves unrelated and malformed settings and rescues paid licenses before state cleanup. Doctor accepts supported sibling core layouts.

Validation on the release source: API26 passed; dashboard32 passed, production build and lint passed; Python2103 passed,17 skipped,99 subtests passed across the full suite and a rerun of9 local-server tests with loopback access. The real-machine timing check used an unchanged fresh local payload outside the source tree. Shell syntax and version synchronization passed. Independent acceptance and security reviews passed. No new dependencies.

The archive is built from public source only. Nested older archives are removed before checksum generation. Live operation and website publication are verified separately after release.
