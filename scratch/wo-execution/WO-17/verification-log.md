# WO-17 Verification Log

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| AC-CUS-001.1 | `test_consent_retains_purpose_evidence_time_scope_and_withdrawal_controls_access` records purpose, evidence, scope, version, and time, then verifies withdrawal. |
| AC-CUS-001.2 | `test_identity_history_preserves_merge_and_unmerge` verifies immutable merge and unmerge history. |
| AC-CUS-001.3 | `test_value_correction_is_immutable_linked_reversal_and_is_idempotent` verifies exact linked reversal, balance projection, immutability, and idempotency. |
| AC-CUS-001.4 | `test_privacy_outcomes_are_scoped_and_retain_legal_evidence` verifies export outcome and legal evidence. |

## Controls verified

- Tenant isolation: in-memory scope checks and PostgreSQL tenant keys restrict all customer facts.
- Authorization and audit: customer commands use deny-by-default actions and append audit records.
- Consent withdrawal: the latest purpose-bound consent fact controls access.
- Ledger integrity: values are append-only and reversals must exactly offset an original tenant-local effect.
- Idempotency and replay: identical in-memory effects return the original fact; PostgreSQL writes emit deterministic durable-outbox event identifiers.
- Migration: `0008_customer_consent_loyalty.up.sql` and `.down.sql` create and remove the customer, identity, consent, privacy, and stored-value schema in dependency order.

## Validation authority

Commit `f7d6e152266b9371941e6128a59653b99d728693` completed successfully in these GitHub Actions workflows:

- Foundation validation: run 34201874124.
- Edge sync validation: run 34201874125.
- Pytest collection diagnostics: run 34201869451.
- Integration execution diagnostics and workspace quality also completed successfully for this commit.

No validation failures occurred during WO-17 execution.
