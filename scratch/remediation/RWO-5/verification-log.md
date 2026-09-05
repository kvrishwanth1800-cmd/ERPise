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

## Pending execution

The first post-change focused run must capture the exact command, exit code, failure details if any, and remediation before final sign-off.
