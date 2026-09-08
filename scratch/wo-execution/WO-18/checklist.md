# WO-18 Checklist

- [x] Read WO-18, the Payment Orchestration requirements document, and both
      linked blueprints (component and feature).
- [x] Inspect the existing `services/foundation` domain/persistence/test
      conventions via the WO-17 customer/consent/loyalty implementation.
- [x] Inspect `crates/transaction-core` to resolve the Rust-vs-Python scope
      question (found unrelated inventory reservation code; no payment
      scaffolding present).
- [x] Implement `foundation/webhook_verifier.py` (ProviderWebhookVerifier).
- [x] Implement `foundation/payment.py` (PaymentOrchestrationService).
- [x] Implement `foundation/payment_persistence.py` (DurablePaymentStore).
- [x] Add migration `0009_payment_orchestration.up.sql` / `.down.sql`.
- [x] Add unit tests `test_webhook_verifier.py` and `test_payment.py`.
- [x] Add integration tests `test_payment_persistence.py`.
- [x] Add Ruff per-file-ignores for the new test files (E501, matching
      existing pattern).
- [x] Commit all changes directly to `feature/phase-1-foundation-rc`.
- [ ] Run Ruff, MyPy, and pytest against the branch (not possible in this
      session; deferred to CI - see implementation-plan.md blockers).
- [x] Leave WO-18 status unchanged.
- [x] Write execution evidence and report back to the user.
