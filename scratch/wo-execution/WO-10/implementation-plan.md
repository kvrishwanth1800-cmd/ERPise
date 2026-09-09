# WO-10 implementation plan

1. Add a typed product domain service with Products, Variants, UOMs, identifiers, lifecycle states, and import validation.
2. Add Product Administration contracts that perform explicit authorization before mutations.
3. Add migration `0004_product_information` for tenant-scoped product, variant, and identifier storage.
4. Add a PostgreSQL product store that writes state, durable events, and audit records atomically.
5. Publish `ProductChanged` for product mutations and `ProductLifecycleChanged` for lifecycle changes.
6. Add focused unit and PostgreSQL integration coverage.
7. Run focused tests before broader quality and integration gates.

## Constraints

- Use the existing PostgreSQL, audit, authorization, and durable outbox patterns.
- Keep all uniqueness checks tenant-scoped.
- A failed bulk import must persist no records and publish no events.
- Do not add catalog, pricing, or inventory behavior.
