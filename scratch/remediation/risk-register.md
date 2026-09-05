# Phase 1 Remediation Risk Register

| ID | Risk | Priority | Owner | Status | Mitigation |
| --- | --- | --- | --- | --- | --- |
| R-01 | Phase 1 is not integrated into a controlled release candidate. | Urgent | RWO-1 | Closed | Candidate `feature/phase-1-foundation-rc` validated at `92f28598ca34e2f7c171da7650072bf80968daa4`. |
| R-02 | Tenant, authorization, and audit evidence are not proven durable. | Urgent | RWO-2 | Open | Add PostgreSQL persistence, migrations, and restart tests. |
| R-03 | Outbox and replay are not proven durable or broker-backed. | Urgent | RWO-3 | Open | Add PostgreSQL/Redpanda delivery and recovery tests. |
| R-04 | Edge synchronization lacks inbox, recovery resolution, and end-to-end reconnect proof. | Urgent | RWO-4 | Open | Complete the edge flow and verify offline/reconnect behavior. |
| R-05 | Restore, rollback, dependency failure, and telemetry drills are not executed. | High | RWO-5 | Open | Execute and record controlled drills. |
| R-06 | Code-to-specification traceability and independent release review are incomplete. | High | RWO-6 / WO-35 | Open | Link code, resolve drift, and record independent reviews. |