# WO-4 checklist

## Evidence discipline

- [x] Record the baseline focused validation commands and successful exit codes.
- [x] Record the first incremental validation failure before remediation.
- [x] Classify the failure before remediation.
- [x] Isolate the failing formatter package as `edge-sync-reconciliation`.
- [x] Identify missing toolchain pinning and missing CI formatter-diff evidence as configuration risks.
- [x] Execute and record the pinned-toolchain formatter diagnostic sequence.
- [x] Capture the exact Clippy package, file, line, lint, message, and exit code.
- [x] Classify the Clippy failure as RWO-4-owned test code and inspect its dependency impact before remediation.
- [x] Replace the unnecessary explicit `drop` with a lexical borrow scope. No lint suppression was added.
- [ ] Record successful focused validation after the lint fix.

## RWO-4 behavior evidence

- [x] Encrypted edge storage is covered by the existing same-key reopen test.
- [x] Offline queue durability and restart recovery are covered by the injected-client retry test.
- [x] Reconnect ordering is covered by the injected-client dispatch test.
- [x] Duplicate reconciliation advances the ordered cursor without a second edge effect.
- [x] Unavailable BFF retry is scheduled and persists across restart.
- [x] Unsafe BFF sequence response enters controlled recovery.
- [x] Tenant mismatch prevents BFF dispatch.
- [x] Freshness is checked after accepted and duplicate reconciliation.
- [ ] Add explicit focused coverage for offline and stale freshness states.
- [ ] Add explicit focused coverage for site, register, device, and credential mismatch.
- [ ] Confirm migration and rollback behavior remains covered after final code changes.

## Final gates

- [ ] Focused edge validation passes after all increments.
- [ ] Workspace quality passes.
- [ ] Foundation validation passes.
- [ ] Delivery manager review sign-off recorded.
- [ ] Technical lead review sign-off recorded.
- [ ] Clean-code review sign-off recorded.
- [ ] WO-32 is formally completed.
- [ ] RWO-5 GO is declared only after all final gates pass.
