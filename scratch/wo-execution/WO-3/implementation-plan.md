# WO-3 implementation plan

## Scope
Implement a typed, in-memory Python application-service baseline for tenant-owned organization hierarchy commands, scope resolution, effective settings, protected deletion, and committed-change event records.

## Affected files
- services/foundation/src/foundation/organization.py
- services/foundation/tests/test_organization.py
- scratch/wo-execution/WO-3/checklist.md
- scratch/wo-execution/WO-3/context.md
- scratch/wo-execution/WO-3/verification-log.md

## Risks and rollback
The in-memory service is a foundation boundary, not persistent production storage. Roll back by reverting this focused feature branch commit. No schema migration or provider integration is introduced.
