# WO-4 implementation plan

1. Establish exact baseline evidence for encrypted edge state and existing recovery behavior.
2. Add a minimal injected BFF reconciliation boundary. Do not introduce HTTP transport, credentials, or new central business behavior.
3. Add focused tests for offline queue durability, ordered reconnect dispatch, duplicate reconciliation, retry across restart, controlled recovery, scope isolation, and freshness.
4. Pin the Rust formatter toolchain to the workspace minimum Rust version and capture formatter diagnostics before remediation. The diagnostic sequence is: check, apply, `git diff --check`, scoped `git diff`, `git status --short`, and recheck.
5. After formatter evidence passes, add the remaining explicit freshness and full device-binding mismatch coverage. Validate each increment with focused tests, formatting, and linting.
6. Run workspace quality and foundation validation only after focused RWO-4 evidence passes. Record review sign-offs before closing WO-32.
