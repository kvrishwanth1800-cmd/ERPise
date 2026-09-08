# WO-13 verification log

## Validated commit

- Implementation commit: `d9780661dd4900a8bfd75a92a74a9e70f5a56e5b`.
- Workspace quality, foundation validation, integration execution diagnostics, edge sync validation, and the remaining workflow gates completed successfully for this commit.
- The integration suite applies migrations through `0007_inventory_ledger`, verifies durable movement events, and reverses migrations in fixture teardown.

## Acceptance mapping

- AC-INV-001.1: Authorized movement posting validates a reason, scope, and non-zero quantity. The ledger records audit evidence and emits `InventoryMoved`.
- AC-INV-001.2: Posted facts have no edit path. `edit_posted_movement` always rejects the request, and duplicate movement identifiers are rejected.
- AC-INV-001.3: Reversals reference an existing movement, retain its product, location, and quantity type, and must exactly negate its quantity.
- AC-INV-001.4: Position projection sums immutable movement facts by product, location, and quantity type. Replay returns the same positions and emits `InventoryPositionRebuilt`.

## Technical review

- The in-memory ledger enforces tenant isolation, authorization, immutability, reversal linkage, and deterministic projection.
- PostgreSQL persists movement facts with tenant/product and reversal foreign keys. Durable movement and rebuild events use schema version `v1`; missing-product writes roll back facts and outbox records atomically.
- Result: all acceptance criteria have automated evidence. GO for dependent reservation work.
