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
- Idempotency and replay: identical in-memory effects return the original fact. PostgreSQL writes emit deterministic durable-outbox event identifiers.
- Migration: `0008_customer_consent_loyalty.up.sql` and `.down.sql` create and remove the customer, identity, consent, privacy, and stored-value schema in dependency order.

## Workspace Quality correction and final result

Earlier evidence incorrectly stated that Workspace Quality passed for the original implementation. The gate on commit `6860db9c85ab1659a57a7304a13b168deb115607` failed in `Lint Python workspace` for `uv run ruff check services --output-format=github`.

The demonstrated root causes were an unused `datetime.UTC` import and over-100-character lines in the WO-17 customer implementation and its persistence and test files. The corrections were limited to Ruff-required formatting and import cleanup, followed by a MyPy-safe `fetchone()` null guard in `customer_persistence.py`.

Final validation passed in [Workspace Quality run 34211676033](https://github.com/kvrishwanth1800-cmd/ERPise/actions/runs/34211676033) for commit `36a1028c4ef08eb7a40c7b11815235ecffe98934`. This run completed TypeScript checks, Python lint, Python type checking, Python tests, Rust checks, and Terraform validation successfully.
