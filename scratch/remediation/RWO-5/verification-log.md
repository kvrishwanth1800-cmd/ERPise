# RWO-5 verification log

## Implemented drill

`services/foundation/tests/test_recovery_drills.py` performs the missing destructive lifecycle drill. It creates two tenants, captures Tenant A settings as recovery input, rolls all Phase 1 migrations down, reapplies all migrations, restores only Tenant A, verifies Tenant B does not exist after restore, and records a successful restore exercise.

The recovery scope also uses existing executable tests for broker retry and restart recovery, replay and duplicate safety, Redpanda publish and consumer-restart recovery, safe telemetry redaction, request-to-event trace evidence, objective-breach alerts, and encrypted edge offline queue recovery.

## Failure and remediation record

| Revision | Command or gate | Exit | Result |
| --- | --- | ---: | --- |
| `f8b4322` | Workspace quality, Python lint | 1 | Ruff I001 reported an unsorted import block in `services/foundation/tests/test_recovery_drills.py:3`. |
| `57f8e0a` | Workspace quality, Python lint | 1 | Ruff I001 remained for the test-file import layout. |
| `de5c9d1` | Workspace quality, Python type check | 1 | MyPy reported `services/foundation/tests/test_recovery_drills.py:73`: `str | None` passed to `OperationsEvidenceService`, which requires `str`. |
| `f61b72b` | Workspace quality, foundation validation, integration execution diagnostics | 0 | The fixture narrows `TEST_DATABASE_URL` before it reaches the test. All final gates passed. |

## Final command evidence

| Drill or gate | Executed command | Exit | Evidence |
| --- | --- | ---: | --- |
| Workspace quality | `pnpm format && pnpm lint && pnpm typecheck && pnpm test && uv run ruff check services --output-format=github && pnpm typecheck:python && uv run pytest services && cargo fmt --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && terraform -chdir=infrastructure/terraform fmt -check && terraform -chdir=infrastructure/terraform init -backend=false && terraform -chdir=infrastructure/terraform validate` | 0 | GitHub Actions run 33989876171. |
| Foundation validation | `cp .env.example .env && docker compose --env-file .env config --quiet && docker compose --env-file .env up --detach --wait --wait-timeout 180 && LOCAL_HEALTH_MAX_ATTEMPTS=30 LOCAL_HEALTH_RETRY_SECONDS=2 sh ./scripts/local-health.sh && docker compose --env-file .env down --volumes` | 0 | GitHub Actions run 33989876181. |
| Recovery and observability integration | `uv run pytest services` with `TEST_DATABASE_URL=postgresql://erpise:change-me-local-only@localhost:5432/erpise` and `TEST_REDPANDA_BOOTSTRAP_SERVERS=localhost:19092` | 0 | GitHub Actions run 33989872900. |

## Acceptance matrix

| Acceptance criterion | Evidence | Result |
| --- | --- | --- |
| AC-OBS-001.1 trace-to-event correlation | `test_operations.py` trace-evidence coverage, exercised by the successful Python-services gate. | Pass |
| AC-OBS-001.2 actionable objective-breach alert | `test_operations.py` objective-alert coverage, exercised by the successful Python-services gate. | Pass |
| AC-OBS-001.3 restore result evidence | New full migration rollback, ordered reapply, Tenant A-only restore, Tenant B isolation, and persisted restore-result drill. | Pass |
| AC-OBS-001.4 telemetry redaction | `test_operations.py` rejects secrets and payment-card values from persisted telemetry, exercised by the successful Python-services gate. | Pass |

## Release decision

RWO-5 is complete at revision `f61b72b0e70cbebfa25d63e808b8e205483cc47a`. The recovery and observability release gate is satisfied. WO-35 may begin its separate traceability and release-review scope.
