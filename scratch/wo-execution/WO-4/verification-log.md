# RWO-4 verification log

## Baseline

- Dependency: WO-31 is complete. The durable outbox and Redpanda delivery validation passed before RWO-4 began.
- Existing edge runtime: `crates/edge-sync/src/lib.rs` already provides encrypted SQLCipher-backed state, device binding, queued operation ordering, duplicate-safe reconciliation outcomes, retry scheduling, controlled recovery, freshness state, and local audit records.
- Evidence rule: Record the exact command, exit code, failing test, and traceback before any failure remediation. Distinguish collection, execution, environment, service configuration, database, and workspace-quality failures.

## Executed baseline evidence

| Command | Exit code | Result | Scope |
| --- | --- | --- | --- |
| `cargo test -p edge-sync` | 0 | Passed | Existing edge-sync unit tests passed. This does not prove each RWO-4 acceptance criterion. |
| `cargo fmt --check -p edge-sync` | 0 | Passed | Existing edge-sync source formatting passed. |
| `cargo clippy -p edge-sync --all-targets -- -D warnings` | 0 | Passed | Existing edge-sync targets passed lint without warnings. |

- CI evidence: the focused Edge sync validation workflow completed successfully for commit `09f511ba595785bdb206167863decbcff3f55529`.
- Workflow: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33985668782
- Failure category: none. The baseline completed successfully.

## Increment 1: injected reconciliation boundary

- Change: Added `edge-sync-reconciliation`, an edge-side controller that dispatches the next ordered `EdgeSyncEnvelope` to an injected BFF client. No HTTP transport, credentials, or central business logic was introduced.
- Focused behavior tests added: reconnect ordering, duplicate reconciliation, retry scheduling across restart, controlled recovery on unsafe BFF response, tenant mismatch rejection before dispatch, and online freshness after reconciliation.

### Failure evidence before remediation

| Field | Evidence |
| --- | --- |
| Workflow | https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33986623269 |
| Job | https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33986623269/job/101361261595 |
| Command | `cargo fmt --check --all` |
| Exit code | 1 |
| Failing test | None. The preceding `cargo test -p edge-sync -p edge-sync-reconciliation` step passed. |
| Traceback or log | `cargo fmt --check --all` was the failed step. The workflow stopped before `cargo clippy --workspace --all-targets -- -D warnings`; GitHub did not publish a source-line formatter diff in the check annotations. |
| Category | Quality-gate formatting failure. It is not a test, environment, database, or service-configuration failure. |
| Proven root cause | The newly created reconciliation source was not formatted according to the repository Rust formatter. |
| Fix | Apply formatting-only changes to `crates/edge-sync-reconciliation/src/lib.rs` in commit `29454b073a32a90f0b59a6bb1a91260816b2000c`. |
| Prevention | Keep `cargo fmt --check --all` in focused validation and do not treat a passed test step as a full validation pass. |

### Post-remediation status

- The follow-up focused validation is queued for commit `29454b073a32a90f0b59a6bb1a91260816b2000c`.
- Workflow: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33986825314
- No result is claimed until the workflow completes.

## Scope discovery

- The repository has no implemented BFF, central reconciliation service, network transport, or central adapter. Its only edge contract is `packages/contracts/src/edge.ts`.
- RWO-4 therefore adds a testable edge-side reconciliation boundary. It does not invent network transport, credentials, or new central business behavior. The boundary dispatches a locally queued `EdgeSyncEnvelope` to an injected reconciliation client and persists the returned outcome through the existing `EdgeStore::reconcile` path.
- The central durable outbox remains RWO-3 scope. RWO-4 validates that the edge sends one ordered envelope and handles accepted, duplicate, retryable, and controlled-recovery outcomes safely.

## Acceptance evidence

| Criterion | Status | Evidence |
| --- | --- | --- |
| AC-EDG-001.1 offline permitted actions | Partial | Durable queue and retry-after-restart tests were added. The revised focused workflow is pending. |
| AC-EDG-001.2 reconnect without duplicate effects | Partial | Ordered dispatch and duplicate reconciliation tests were added. The revised focused workflow is pending. |
| AC-EDG-001.3 operator freshness state | Partial | Online freshness is checked after reconciliation. Explicit offline and stale focused tests remain pending. |
| AC-EDG-001.4 controlled recovery | Partial | Unsafe BFF response enters controlled recovery. The revised focused workflow is pending. |

## Lessons learned

- Capture the actual failing command and complete diagnostics before changing code or configuration.
- Validate changes in small increments and rerun relevant tests immediately.
- Record root cause, fix, verification, and prevention for each failure.
- Full workspace and foundation validation are final gates. They do not replace focused acceptance evidence.
