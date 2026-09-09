# RWO-6 Repository-Backed Source Traceability

## Purpose and constraint

This file is the durable source traceability record for the Phase 1 candidate. It is authoritative while Software Factory code-link registration is unavailable.

The registration retry was performed after validation of `5650295fdab4a6476ec4dae8390d3ce3de4f0023`. Every Phase 1 blueprint registration failed because the platform returned: `line range(s) do not match any code chunks`. This is an indexing/tool constraint. It does not indicate missing repository implementation.

## Exact source map

| Feature | Source path, symbol, and lines | Tests | Primary commits |
| --- | --- | --- | --- |
| Platform Bootstrap | `compose.yaml` lines 1-89, local dependency services and health checks. `packages/contracts/src/index.ts` lines 1-114, shared health, verification, command, problem, and event contracts. `.github/workflows/quality.yml` lines 1-117, workspace gates. | `packages/contracts/src/index.test.ts` lines 1-86; `services/foundation/tests/test_health.py` lines 1-7 | `a25765dc5afca130d94517b88b76183247a624bc`; `e433f05d8e60e544c66b4ca42d8a45931479b3d6` |
| Tenant and Organization | `services/foundation/src/foundation/organization.py` lines 23-168, `ScopeResolver` and `OrganizationHierarchyService`. `services/foundation/src/foundation/persistence.py` lines 20-108 and 163-184, durable hierarchy state. `services/foundation/migrations/0002_foundation_durable_state.up.sql`, organization tables and constraints. | `test_organization.py` lines 1-55; `test_durable_persistence.py` lines 35-88 and 117-172 | `a5cbb7fa3da31e9c20a16901844b6be2d129759c`; `b845e998822da89f53580dd256199676bc19c99d` |
| Identity and Access | `services/foundation/src/foundation/access.py` lines 19-123, `SessionRevocationService` and `AuthorizationService`. `persistence.py` lines 110-133, durable grants and decisions. Migration `0002_foundation_durable_state.up.sql`, authorization tables. | `test_access.py` lines 1-68; `test_durable_persistence.py` lines 90-115 | `ee04ae557344fe3857e51c8e967a5b3ee5898d9e`; `b845e998822da89f53580dd256199676bc19c99d` |
| Audit, Policy and Workflow | `services/foundation/src/foundation/audit.py` lines 18-139, `AuditRecorder` and `ApprovalWorkflowService`. `persistence.py` lines 135-161, durable audit and approval state. Migration `0002_foundation_durable_state.up.sql`, immutable audit trigger. | `test_audit.py` lines 1-57; `test_durable_persistence.py` lines 90-115 | `147ec2835e5d4cb077d85c889f0a20cb0537e239`; `b845e998822da89f53580dd256199676bc19c99d` |
| Event Platform | `packages/contracts/src/index.ts` lines 25-100, `CommandEnvelope`, `DomainEvent`, and validation. `services/foundation/src/foundation/durable_outbox.py` lines 44-194, atomic commit, publish retry, dead letters, idempotent consumption, and replay. `redpanda.py` lines 16-84. Migration `0003_durable_outbox_replay.up.sql` lines 1-54. | `test_outbox.py` lines 1-98; `test_durable_outbox.py` lines 1-139; `test_redpanda_outbox.py` lines 1-67 | RWO-3 implementation evidence is mapped to candidate revision `6aca913dfa94f08f045c06781ec12ca7146f77a5` |
| Observability and Operations | `services/foundation/src/foundation/operations.py` lines 18-221, `OperationsEvidenceService`, redaction, alerts, restore evidence, and retention. Migration `0001_operations_evidence.up.sql` lines 1-31. | `test_operations.py` lines 1-115; `test_recovery_drills.py` lines 1-83 | `f8b4322ab21ee6919e424ab6aeab3ae9bebf3633`; `de5c9d132bd6632422ccffe9f94b8a10af1c6b59`; `f61b72b0e70cbebfa25d63e808b8e205483cc47a`; `6aca913dfa94f08f045c06781ec12ca7146f77a5` |
| Edge and Offline Runtime | `crates/edge-sync/src/lib.rs` lines 1-520, `EdgeStore`, encrypted local persistence, `queue_sale`, `reconcile`, and freshness. `crates/edge-sync-reconciliation/src/lib.rs` lines 1-236, `EdgeSynchronizationController` and `BffReconciliationClient`. `packages/contracts/src/edge.ts` lines 1-37, `EdgeSyncEnvelope` and `EdgeOperationReconciled`. | Embedded tests in `edge-sync/src/lib.rs` and `edge-sync-reconciliation/src/lib.rs`; `.github/workflows/edge-sync-validation.yml` lines 1-63 | RWO-4 implementation evidence is mapped to candidate revision `6aca913dfa94f08f045c06781ec12ca7146f77a5` |

## Validation references

- Workspace quality `33989876171`: success, exit 0.
- Foundation validation `33989876181`: success, exit 0.
- Integration execution `33989872900`: success, exit 0.
- Current audit commit foundation validation `33990604144`: success.
- Current audit commit edge sync validation `33990604378`: success.
