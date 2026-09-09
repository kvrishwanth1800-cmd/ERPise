# WO-11 context

## Scope

WO-11 implements tenant-scoped assortment publication and eligibility preview. It excludes price rules.

## Approved decisions

- Scopes may include store, channel, and segment.
- A matching assortment is effective at `effective_from <= time < effective_until` when an end exists.
- Most-specific scope wins. Ties use the latest effective start time, then assortment ID in ascending lexical order.
- Preview and publication must use one eligibility evaluator.

## Existing foundations reused

- Tenant scope: `ScopeContext`.
- Deny-by-default authorization: `AuthorizationService` with `assortment.write`.
- Append-only audit: `AuditRecorder`.
- Product keys: the Product Information migration.
