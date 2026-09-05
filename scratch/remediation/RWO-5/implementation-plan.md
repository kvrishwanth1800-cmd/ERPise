# RWO-5 implementation plan

1. Execute automated drills against the local PostgreSQL and Redpanda services.
2. Add an integrated destructive migration rollback, reapply, tenant-scoped restore, and restore-evidence test.
3. Reuse existing durable outbox, Redpanda restart, replay, duplicate-safety, telemetry-redaction, trace, alert, and edge queue tests as drills.
4. Add an operator runbook with safe local commands and recovery boundaries.
5. Record exact CI validation evidence and close R-05 only after all drill gates pass.
