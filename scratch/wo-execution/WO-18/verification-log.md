# WO-18 Verification Log

## Commits

| Commit | Description |
| --- | --- |
| `155af44391dec8b673c6f384972d921b89aaa187` | Core implementation: webhook_verifier.py, payment.py, payment_persistence.py, migration 0009 up/down. |
| `ee02cf57d458ace06086bb1415b0f901dc5643b3` | Tests: test_webhook_verifier.py, test_payment.py, test_payment_persistence.py. |
| `4550ffefa18e2b2e2ee021fd4423d0e8a6cda5d6` | pyproject.toml: Ruff per-file-ignores (E501) for the two new test files. |

## Acceptance criteria coverage

| Criterion | Coverage |
| --- | --- |
| AC-PAY-001.1 (idempotent retry, no duplicate capture/refund) | `test_capture_is_idempotent_by_key_and_requires_a_verified_callback`, `test_refund_cannot_exceed_captured_amount_and_is_idempotent` |
| AC-PAY-001.2 (only verified callbacks confirm state) | `test_capture_rejects_an_unverified_or_mismatched_callback`, `test_webhook_verifier.py` (signature tests) |
| AC-PAY-001.3 (no PAN/CVV storage or logging) | `test_payment_dataclasses_never_carry_cardholder_data_fields`; migration 0009 has no PAN/CVV columns; webhook verifier only carries provider_reference/amount_cents |
| AC-PAY-001.4 (append-only, traceable records) | `test_payment_transactions_are_append_only`; migration 0009 reject-mutation triggers on all four tables; every write goes through DurableOutboxStore with a trace_id |
| AC-PAY-001.5 (settlement reconciliation / exception reporting) | `test_settlement_exception_is_idempotent_and_immutable`, `test_payment_facts_and_events_are_durable_and_tenant_scoped` |

## Automated validation

Not executed in this session: no connected code-execution sandbox was
available for Ruff, MyPy, or pytest. This is recorded as an open item;
the branch's CI workflows (`foundation.yml`, `quality.yml`,
`pytest-collection.yml`, `integration-execution.yml`) will run against
these commits on the next push/PR trigger and should be checked before
this work order's evidence is treated as fully verified.

## WO-18 status

Left unchanged (still `in_progress`) per instruction; not modified by this
session.
