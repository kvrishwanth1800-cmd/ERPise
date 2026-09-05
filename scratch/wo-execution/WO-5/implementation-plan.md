# WO-5 implementation plan

1. Implement append-only audit evidence with the required accountability fields.
2. Implement approval creation, eligible approval resolution, self-approval denial, and configured timeout outcomes.
3. Record every workflow transition as audit evidence and publish completion facts through the service outbox.
4. Run deterministic format, lint, type, test, and foundation validation gates.
