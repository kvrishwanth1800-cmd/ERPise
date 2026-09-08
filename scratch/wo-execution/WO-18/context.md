# WO-18 Context

## Work order

- WO-18: Implement payment orchestration local fake.
- Blocked by WO-17 (Implement customer consent and loyalty) - completed.
- Blocks WO-19.

## Inputs

- Payment Orchestration requirements document (REQ-PAY-001, acceptance
  criteria AC-PAY-001.1 through AC-PAY-001.5).
- Payment Orchestration component blueprint: PaymentStateMachine (Rust
  Transaction Services / PostgreSQL) and ProviderWebhookVerifier (Python
  Application Services); ADR-001 verified-evidence-controls-state.
- Payment Orchestration feature blueprint: PaymentApi (API Gateway/BFF),
  publishes PaymentChanged and SettlementReconciliationReported.
- Existing services/foundation patterns from WO-17 (customer.py,
  customer_persistence.py, durable_outbox.py, migration
  0008_customer_consent_loyalty).

## Boundaries

- No change to WO-18 status (left as-is per instruction).
- Commit directly to `feature/phase-1-foundation-rc`.
- No production deployment or infrastructure change.

## Traceability

- Implementation commits: `155af44391dec8b673c6f384972d921b89aaa187`,
  `ee02cf57d458ace06086bb1415b0f901dc5643b3`,
  `4550ffefa18e2b2e2ee021fd4423d0e8a6cda5d6`.
- Branch: `feature/phase-1-foundation-rc`.
