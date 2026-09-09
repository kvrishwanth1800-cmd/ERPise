# WO-15 verification log

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| AC-RCV-001.1 | `test_receipt_captures_evidence_and_requests_putaway_before_stock_effect` verifies quantity, cost, batch, serial, expiry, stock movement, receipt audit evidence, and putaway request. |
| AC-RCV-001.2 | `test_discrepancy_is_recorded_before_receipt_completion` verifies discrepancy quantity and audit ordering. |
| AC-RCV-001.3 | `test_duplicate_submission_has_one_logical_outcome_and_one_stock_effect` verifies retry idempotency, a single stock effect, and one receipt event set. |
| AC-RCV-001.4 | `test_reversal_preserves_receipt_evidence_and_creates_corrective_movement_once` verifies original evidence retention, idempotent reversal, and zero projected quantity after correction. |

## Additional safety coverage

- `test_tenant_isolation_and_unknown_tenant_reversal_are_rejected` verifies that an authorized tenant-B receiver cannot reverse tenant-A receipt evidence.
- `ReceivingService` requires `receiving.write`; the reused inventory ledger independently requires `inventory.write` and owns stock facts and reversals.
- Receipt outcomes use `(tenant_id, receipt_id)` as their idempotency identity. Corrections use the ledger reversal path, which preserves append-only stock truth.

## CI validation for code commit `2cdaf29b53a4f1f94ca3e981fae877d75a426cd9`

- Foundation validation, run 34188567944: passed. The compose configuration and local health checks succeeded.
- Edge sync validation, run 34188567957: passed. Formatting, tests, and lint succeeded.
- Integration execution diagnostics, run 34188564638: passed. The exact integration test command succeeded.

## Review conclusion

The implementation meets AC-RCV-001.1 through AC-RCV-001.4 within the established in-memory Foundation application-service architecture. No documented requirement or blueprint mandates PostgreSQL persistence for receipt evidence in this work order. The existing durable inventory movement persistence remains the stock truth boundary. A future durable receipt-evidence requirement should define its schema, transaction boundary, and durable event contract before a migration is introduced.
