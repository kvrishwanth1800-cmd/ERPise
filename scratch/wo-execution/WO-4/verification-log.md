# RWO-4 verification log

## Baseline

- Dependency: WO-31 is complete. Durable outbox and Redpanda delivery validation passed before RWO-4 began.
- Existing edge runtime: `crates/edge-sync/src/lib.rs` provides encrypted SQLCipher-backed state, device binding, queued operation ordering, duplicate-safe reconciliation outcomes, retry scheduling, controlled recovery, freshness state, and local audit records.
- Evidence rule: record the exact command, exit code, failing test, and diagnostic before failure remediation. Classify collection, execution, environment, service configuration, database, and quality failures separately.

## Baseline focused evidence

| Command | Exit code | Result |
| --- | --- | --- |
| `cargo test -p edge-sync` | 0 | Passed. |
| `cargo fmt --check -p edge-sync` | 0 | Passed. |
| `cargo clippy -p edge-sync --all-targets -- -D warnings` | 0 | Passed. |

Initial focused CI: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33985668782

## Reconciliation increment

- Added `edge-sync-reconciliation`, an edge-side controller that sends the next ordered `EdgeSyncEnvelope` through an injected BFF client and persists the bounded result through `EdgeStore`.
- No HTTP transport, credentials, or central business logic was added.
- Covered behavior: ordered reconnect dispatch, duplicate reconciliation, BFF-unavailable retry and restart, unsafe response controlled recovery, tenant mismatch dispatch rejection, and online freshness after reconciliation.

## Formatter root-cause evidence

| Command or observation | Exit code / result | Evidence |
| --- | --- | --- |
| `cargo fmt --check -p edge-sync` | 0 | Passed in the isolated run. |
| `cargo fmt --check -p edge-sync-reconciliation` under moving stable 1.98.1 | 1 | Failed. |
| `cargo fmt -p edge-sync-reconciliation` then scoped `git diff --exit-code` | 1 | Proved rustfmt changed committed reconciliation source. |
| Pinned Rust 1.85.0 diagnostic: initial check, apply, SHA-256, `git diff --check`, scoped diff, status, and recheck | 0 | Passed in a clean checkout, without source-cache restore. |

Pinned diagnostic run: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33987710675

Finding: the reproducible formatter prevention measure is the Rust 1.85.0 pin, which matches the workspace minimum. The old 1.98.1-generated diff was not retained in the historical logs, so its exact text is unresolved. The pinned run proves the current committed source has no formatter diff under the intended toolchain.

## Clippy root-cause evidence and remediation

Failed focused run: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33987833700

| Field | Evidence |
| --- | --- |
| Command | `cargo clippy -p edge-sync -p edge-sync-reconciliation --all-targets -- -D warnings` |
| Exit code | 101 |
| Package | `edge-sync-reconciliation` (lib test) |
| File and line | `crates/edge-sync-reconciliation/src/lib.rs:152:9` |
| Lint | `clippy::drop_non_drop` |
| Full error | `call to std::mem::drop with a value that does not implement Drop. Dropping such a type only extends its contained lifetimes` |
| Trigger | `drop(controller);` where `controller` is `EdgeSynchronizationController<'_, tests::FakeBff>` |
| Classification | RWO-4-owned test-code quality failure. It is not a collection, execution, database, environment, broker, or dependency failure. |
| Dependency impact | None. The reconciliation crate depends only on `edge-sync`; this lint operates on local test code and does not require a dependency update. |

Root cause: the test explicitly called `drop(controller)` to release mutable borrows of the store and fake BFF before its assertions. `EdgeSynchronizationController` has no `Drop` implementation, so the explicit call is ineffective for resource management and Clippy correctly rejects it under `-D warnings`.

Fix: wrap the controller and synchronization assertions in a lexical block. The block ends the mutable borrows before the BFF and store assertions. No lint suppression was used.

Prevention: focused CI remains strict for both edge crates and saves complete Clippy stderr as the `edge-clippy-stderr` artifact on every run. This preserves diagnostics even if the rendered job log or annotation is truncated.

## Pending post-fix validation

The post-fix workflow must exit 0 for all of the following before any new RWO-4 increment:

1. `cargo fmt -p edge-sync -- --check`
2. Reconciliation formatter diagnostic sequence: check, apply, SHA-256, `git diff --check`, scoped diff, status, recheck
3. `cargo test -p edge-sync -p edge-sync-reconciliation`
4. `cargo clippy -p edge-sync -p edge-sync-reconciliation --all-targets -- -D warnings`
5. Complete focused RWO-4 validation workflow

## Acceptance status

| Criterion | Status | Evidence |
| --- | --- | --- |
| AC-EDG-001.1 offline permitted actions | Partial | Durable queue and retry-after-restart tests exist. |
| AC-EDG-001.2 reconnect without duplicate effects | Partial | Ordered dispatch and duplicate reconciliation tests exist. |
| AC-EDG-001.3 operator freshness | Partial | Online freshness is tested; explicit offline and stale tests remain. |
| AC-EDG-001.4 controlled recovery | Partial | Unsafe BFF response enters controlled recovery. |

WO-32 remains in progress. RWO-5 has not started.
