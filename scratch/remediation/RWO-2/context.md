# RWO-2 context

- Work order: WO-30.
- Prerequisite: WO-34 completed with release-candidate validation.
- Active branch: `feature/phase-1-foundation-rc`.
- Baseline reviewed: `15a5281d5eccc7d343ee9941559da94b3adaba16`.
- Existing PostgreSQL service is available in `compose.yaml`, but foundation domain services currently use process-local collections.
- Existing `0001_operations_evidence` migration is insufficient for tenant hierarchy, authorization, immutable audit, and hierarchy outbox persistence.
