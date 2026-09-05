# RWO-4 verification log

## Baseline

- Dependency: WO-31 is complete. The durable outbox and Redpanda delivery validation passed before RWO-4 began.
- Existing edge runtime: `crates/edge-sync/src/lib.rs` provides encrypted SQLCipher-backed state, device binding, queued operation ordering, duplicate-safe reconciliation outcomes, retry scheduling, controlled recovery, freshness state, and local audit records.
- Evidence rule: Record the exact command, exit code, failing test, and traceback before any failure remediation. Distinguish collection, execution, environment, service configuration, database, and workspace-quality failures.

## Executed baseline evidence

| Command | Exit code | Result | Scope |
| --- | --- | --- | --- |
| `cargo test -p edge-sync` | 0 | Passed | Existing edge-sync unit tests passed. This does not prove each RWO-4 acceptance criterion. |
| `cargo fmt --check -p edge-sync` | 0 | Passed | Existing edge-sync source formatting passed. |
| `cargo clippy -p edge-sync --all-targets -- -D warnings` | 0 | Passed | Existing edge-sync targets passed lint without warnings. |

- CI evidence: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33985668782

## Increment 1: injected reconciliation boundary

- Change: Added `edge-sync-reconciliation`, an edge-side controller that dispatches the next ordered `EdgeSyncEnvelope` to an injected BFF client. No HTTP transport, credentials, or central business logic was introduced.
- Focused behavior tests added: reconnect ordering, duplicate reconciliation, retry scheduling across restart, controlled recovery on unsafe BFF response, tenant mismatch rejection before dispatch, and online freshness after reconciliation.

## Formatter root-cause analysis: in progress

### Direct evidence collected

| Command or observation | Exit code / result | Evidence |
| --- | --- | --- |
| `cargo test -p edge-sync -p edge-sync-reconciliation` | 0 | Passed in repeated focused CI runs. |
| `cargo fmt --check -p edge-sync` | 0 | Passed in the per-package run. |
| `cargo fmt --check -p edge-sync-reconciliation` | 1 | Failed in the per-package run. |
| `cargo fmt -p edge-sync-reconciliation` then `git diff --exit-code -- crates/edge-sync-reconciliation` | 1 | Failed, proving the formatter modifies a committed file in the reconciliation crate. The prior workflow did not print the resulting diff. |
| `cargo clippy -p edge-sync -p edge-sync-reconciliation --all-targets -- -D warnings` | Not executed | Skipped after formatter failure. |

- Per-package workflow: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33987094295
- Diff diagnostic workflow: https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/33987433801
- Failure category: formatter and toolchain/configuration evidence gap. It is not a test, environment, database, or service-configuration failure.

### Findings before remediation

- The formatter failure is limited to the RWO-4-owned reconciliation crate. `edge-sync` passes formatting.
- CI used moving `stable` and installed Rust 1.98.1. The workspace only declared `rust-version = 1.85`; it did not pin Rust or rustfmt with `rust-toolchain.toml`.
- No generated Rust files, build scripts, feature-dependent generated code, or macro-expansion formatting paths exist in the reconciliation crate.
- The workflow checks a clean Actions checkout. It does not restore a source cache. The failed formatter-apply step proves the formatter creates an uncommitted source diff in CI.
- The earlier workflow did not preserve the exact diff, tool versions, line ending state, or final recheck. No code change will be made until that diagnostic sequence runs with the pinned formatter.

### Corrective action and prevention

- Fix under test: pin Rust and rustfmt to 1.85.0, the declared workspace minimum, using `rust-toolchain.toml`.
- Add regression diagnostics to CI: record tool versions and workspace metadata; run formatter check, formatter apply, `git diff --check`, scoped diff, status, and final check for the reconciliation crate.
- The diagnostic failure remains a gate. CI succeeds only when the initial and final formatter checks, formatter apply, and diff checks all exit 0.
- No unrelated source file has been modified by this corrective configuration commit. Changed files are the toolchain, focused workflow, and WO-4 execution documentation.

## Scope discovery

- The repository has no implemented BFF, central reconciliation service, network transport, or central adapter. Its only edge contract is `packages/contracts/src/edge.ts`.
- RWO-4 therefore adds a testable edge-side reconciliation boundary. It does not invent network transport, credentials, or new central business behavior.

## Acceptance evidence

| Criterion | Status | Evidence |
| --- | --- | --- |
| AC-EDG-001.1 offline permitted actions | Partial | Durable queue and retry-after-restart tests exist. Pinned focused validation is pending. |
| AC-EDG-001.2 reconnect without duplicate effects | Partial | Ordered dispatch and duplicate reconciliation tests exist. Pinned focused validation is pending. |
| AC-EDG-001.3 operator freshness state | Partial | Online freshness is checked after reconciliation. Explicit offline and stale focused tests remain pending. |
| AC-EDG-001.4 controlled recovery | Partial | Unsafe BFF response enters controlled recovery. Pinned focused validation is pending. |

## Lessons learned

- Capture the actual failing command and complete diagnostics before changing code or configuration.
- Pin formatter versions before relying on formatting evidence across environments.
- Full workspace and foundation validation are final gates. They do not replace focused acceptance evidence.
