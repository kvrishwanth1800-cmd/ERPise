# RWO-2 implementation plan

## Objective
Replace the Phase 1 in-memory foundation state with PostgreSQL-backed repositories for tenant hierarchy, hierarchy settings, authorization evidence, immutable audit evidence, and committed hierarchy-change outbox records.

## Observed baseline
- `organization.py` stores records, dependent-operation counts, and change events in process memory.
- `access.py` stores grants, duty assignments, revocations, and authorization decisions in process memory.
- `audit.py` stores audit and approval state in process memory.
- `outbox.py` is explicitly an in-memory outbox. RWO-2 owns only hierarchy writes and their transaction-bound outbox records. Broker delivery and replay remain RWO-3 scope.
- The existing operations-evidence migration does not define the required foundation tables.

## Delivery steps
1. Add a reversible versioned PostgreSQL migration with tenant-scoped keys, foreign keys, indexes, immutable audit triggers, and protected hierarchy deletion.
2. Add a transaction-scoped persistence boundary that writes hierarchy state and `OrganizationChanged` records atomically.
3. Add durable repositories for authorization grants, revocations, decisions, audit records, and approval state.
4. Add integration, rollback, failure, isolation, and concurrency tests.
5. Run quality and foundation validation, then record evidence and reviews.

## Boundaries
No broker delivery, consumer progress, replay recovery, public API, production deployment, or irreversible migration is included.