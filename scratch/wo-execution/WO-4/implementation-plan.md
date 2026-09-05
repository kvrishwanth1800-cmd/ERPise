# WO-4 implementation plan

1. Establish exact baseline evidence for encrypted edge state and existing recovery behavior.
2. Add a minimal injected BFF reconciliation boundary. Do not introduce HTTP transport, credentials, or central business behavior.
3. Add focused tests for offline queue durability, ordered reconnect dispatch, duplicate reconciliation, retry across restart, controlled recovery, scope isolation, and freshness.
4. Pin the Rust formatter toolchain to the workspace minimum and capture formatter diagnostics before remediation. The sequence is: check, apply, `git diff --check`, scoped `git diff`, `git status --short`, and recheck.
5. Resolve the RWO-4-owned `clippy::drop_non_drop` finding by ending the test controller borrow in a lexical scope. Do not suppress the lint. This permits inspection of the borrowed store and fake BFF after synchronization without calling `drop` on a type that has no destructor.
6. Keep complete Clippy stderr as a CI artifact on every focused validation run. This prevents future log-display truncation from blocking root-cause analysis.
7. Run formatter checks, Clippy, tests, and the complete focused workflow before the next behavior increment.
8. After focused evidence passes, add remaining explicit freshness, device-binding mismatch, and migration/rollback coverage in separate increments. Run workspace quality and foundation validation only after all focused increments pass.
