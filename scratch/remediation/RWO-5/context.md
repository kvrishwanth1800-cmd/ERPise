# RWO-5 context

WO-33 verifies Phase 1 recovery and observability against the integrated local PostgreSQL, Redpanda, Python, and Rust release candidate. The work is limited to drills, focused drill automation, evidence, and runbooks. It does not add product behavior or start WO-35.

Existing durable persistence, outbox/replay, operations-evidence, and edge-sync implementations supply the recovery mechanisms. The identified gap was a single integrated full migration rollback, tenant-scoped restore, and restore-evidence drill. `test_recovery_drills.py` closes that gap.
