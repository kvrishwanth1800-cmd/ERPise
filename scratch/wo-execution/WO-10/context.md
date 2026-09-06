# WO-10 Product Information context

## Scope

Implement tenant-scoped Product Information only:

- Products and variants
- Units of measure
- Identifiers and barcodes
- Lifecycle status
- Required-field and uniqueness validation
- Atomic imports
- PostgreSQL persistence and migration
- Authorization, audit, and durable events

## Excluded scope

Catalog eligibility, pricing, inventory, supplier governance, and future modules are excluded.

## Baseline patterns

- `ScopeContext` and `AuthorizationService` provide deny-by-default tenant-scoped authorization.
- `DurableOutboxStore` commits a business write and versioned event in one PostgreSQL transaction and creates audit evidence.
- Existing migrations are paired `up` and `down` SQL files and integration tests apply them explicitly.

## Traceability

- REQ-PROD-001
- AC-PROD-001.1: required unit and lifecycle validation
- AC-PROD-001.2: identifier uniqueness within a tenant
- AC-PROD-001.3: lifecycle restrictions for dependent operations
- AC-PROD-001.4: no partial changes from a failed import

## Evidence rule

Record the exact command, output, error, and root cause before changing code to correct a validation failure.
