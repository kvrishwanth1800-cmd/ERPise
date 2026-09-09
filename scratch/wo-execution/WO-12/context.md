# WO-12 implementation context

## Scope

Implement deterministic tenant-scoped price resolution, promotions, coupon commitment, and tax-compatible quote output. Tax filing and tax-rate calculation remain outside this work order.

## Decisions from the master plan

- Price precedence: most specific matching store, channel, and segment scope, then latest effective start, then lexical price-list identifier.
- Promotion precedence: higher priority, then more specific scope, then latest effective start, then lexical promotion identifier.
- Stacking: stackable percentage promotions apply in that deterministic order. If any eligible non-stackable promotion exists, only the highest-ranked non-stackable promotion applies.
- Money: decimal values round to two currency fraction digits with `ROUND_HALF_UP` after every promotion step. The calculated net amount is the tax-compatible tax basis.
- Coupon commitment: the same tenant/coupon and quote is idempotent. A different quote for an already committed coupon is rejected.

## Reused foundations

Product foreign keys, tenant scope, authorization, audit recording, durable outbox, reversible PostgreSQL migrations, and WO-11 evidence-first validation conventions.
