# WO-6 implementation plan

## Goal

Create persistent, tenant-scoped operations evidence for Program A. The service must correlate requests to committed v1 events, record restore outcomes, create deterministic SLO alerts, and redact unsafe telemetry data.

## Complete slice

1. Add a PostgreSQL adapter and reversible SQL migration pair.
2. Persist append-only trace-event, telemetry, and restore-exercise evidence.
3. Persist open actionable alerts only for measured SLO breaches.
4. Use tenant ID in every read, write, index, and retention operation.
5. Run PostgreSQL integration tests in Workspace quality.

## Boundaries

- Scope is operations evidence and auditable replay support only.
- No production monitoring configuration, external alert provider, API/BFF endpoint, payment flow, or Program B work is included.
- The existing event envelope remains versioned and unchanged.
