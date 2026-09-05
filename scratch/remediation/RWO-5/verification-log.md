# RWO-5 verification log

## Baseline findings

- Database migration rollback was individually covered for migrations 0002 and 0003. The release candidate lacked one complete 0003 -> 0002 -> 0001 rollback and 0001 -> 0002 -> 0003 reapply drill.
- The durable outbox already covers broker retry after restart, dead letters, duplicate-safe consumption, replay without unsafe external effects, and tenant-scoped consumption.
- Redpanda tests already cover broker publish, duplicate delivery, and consumer restart recovery.
- Operations tests already cover restore records, safe telemetry redaction, trace-to-event correlation, and actionable objective alerts.
- Edge tests already cover encrypted offline queue persistence, reconnect ordering, duplicate safety, retry after restart, controlled recovery, and scope isolation.

## Implemented drill

`services/foundation/tests/test_recovery_drills.py` adds the missing destructive migration drill. It creates two tenants, captures Tenant A settings as recovery input, rolls all Phase 1 migrations down, reapplies all migrations, restores only Tenant A, verifies Tenant B does not exist after restore, and records a successful restore exercise.

No application logic was changed. The added code is a recovery drill test and operational runbook only.

## Validation records

- `f8b4322`: Foundation validation and integration diagnostics passed. Workspace quality failed only on Ruff I001 in the new drill file, exit code 1.
- `57f8e0a`: Foundation validation and integration diagnostics passed. Workspace quality again reported Ruff I001, exit code 1.
- `de5c9d1`: Ruff passed. Workspace quality then failed at MyPy on `services/foundation/tests/test_recovery_drills.py:73`: `str | None` passed to `OperationsEvidenceService`, which requires `str`, exit code 1. Foundation validation and integration diagnostics passed.
- `8e2c9e0` (pending): narrows the environment value in the fixture before it reaches the test. The next complete validation run is required before sign-off.
