# WO-18 Implementation Plan

## Scope decision: Python-only, services/foundation

The component blueprint names a Rust `PaymentStateMachine` (Transaction
Services / PostgreSQL) and a Python `ProviderWebhookVerifier` (Application
Services). The `crates/transaction-core` Rust crate was inspected and found
to contain an unrelated in-memory `ReservationEngine` (inventory
reservation state machine for stock, not payments). It has no existing
payment scaffolding.

Given the work order explicitly instructs following the existing
`services/foundation` Python patterns and choosing the smallest coherent
scope, this implementation adds the full payment orchestration local fake
(state machine, webhook verifier, and persistence) in
`services/foundation` only, mirroring the customer/consent/loyalty (WO-17)
module shape. See "Unresolved blockers" below for the resulting
blueprint-architecture note.

## Design

- `foundation/webhook_verifier.py`: `ProviderCallback` value object and
  `ProviderWebhookVerifier`, an HMAC-SHA256 signer/verifier over
  (tenant_id, kind, subject_id, provider_reference, amount_cents). No
  cardholder data ever passes through this boundary.
- `foundation/payment.py`: `PaymentOrchestrationService` with in-memory
  idempotency-by-key dictionaries (same shape as `CustomerConsentService`).
  `create_intent`, `capture`, `refund`, `report_settlement_exception`.
  Capture and refund require a verified `ProviderCallback` matching the
  tenant, subject, and amount before any state changes; unverified
  evidence raises `PaymentValidationError` and never mutates state
  (AC-PAY-001.2). Retries with the same idempotency key return the first
  result unchanged (AC-PAY-001.1). Refund amount is checked against
  captured amount minus already-refunded amount.
- `foundation/payment_persistence.py`: `DurablePaymentStore` wraps
  `DurableOutboxStore` exactly like `customer_persistence.py`, committing
  each business write and its `PaymentChanged` /
  `SettlementReconciliationReported` outbox event in one transaction.
- Migration `0009_payment_orchestration`: `payment_intents`,
  `payment_transactions`, `payment_refunds`,
  `payment_settlement_exceptions` tables. Tenant-scoped composite primary
  keys, idempotency-key unique constraints, no PAN/CVV columns anywhere
  (AC-PAY-001.3), and append-only triggers mirroring migration 0002's
  `reject_audit_mutation()` pattern (AC-PAY-001.4).

## Tests added

- `test_webhook_verifier.py`: signature acceptance, tamper rejection,
  wrong-secret rejection.
- `test_payment.py`: idempotent capture/refund, unverified-callback
  rejection, refund-exceeds-captured-amount rejection, settlement
  exception idempotency/immutability, tenant scope and authorization
  denial, and a parametrized guard asserting no payment dataclass field
  name contains "pan", "card", or "cvv".
- `test_payment_persistence.py` (integration, `TEST_DATABASE_URL`-gated):
  durable commit with outbox events, tenant isolation, append-only
  enforcement via the database trigger.

## Unresolved blockers / notes

1. No Ruff, MyPy, or pytest execution was available in this session (no
   connected sandbox to run these tools against the repository). All new
   code was written by hand to match the strict-mode, line-length-100,
   `from __future__ import annotations` conventions observed in
   `customer.py` / `customer_persistence.py`, and per-file-ignores were
   added for the two new test files following the existing E501 pattern.
   This should be verified by CI (`.github/workflows/foundation.yml`,
   `quality.yml`) on the next push/PR run; if CI surfaces issues they will
   need a follow-up fix commit.
2. The blueprint's Rust `PaymentStateMachine` component was not
   implemented; only the Python-side state machine and webhook verifier
   were built (see scope decision above). If the intended architecture
   requires the state machine to live in `crates/transaction-core`, that
   is out of scope for this work order as executed and should be raised
   as a blueprint/architecture follow-up.
