# WO-16 checklist

- [x] Blind-count start response withholds expected quantity from the counter.
- [x] Count submission computes variance from the ledger projection and requires approval only when the configured threshold is exceeded.
- [x] Eligible approval posts one immutable corrective ledger movement.
- [x] Expiry and recall create idempotent sale-ineligibility facts.
- [x] Quarantine and disposal retain scope, reason, evidence ID, audit evidence, and linked corrective movements.
- [x] Tenant-scoped identifiers, authorization, audit records, and event emission are covered by focused tests.
- [x] No migration was added. WO-16 is implemented in the existing in-memory Foundation application-service boundary. The durable inventory ledger schema remains the stock-truth boundary. No requirement or blueprint defines durable count or safety-evidence storage, its transaction boundary, or its event schema.

## Implementation commits

- `4d106521af5e55b984e4fc7ba26f12b49a62e8ab`
- `c6ec21997780fa729b4691c223c5ca3099b4194c`
- `20e99066d3f7cade2f0e26eda258535b227bcb96`
- `bddddaa57184d3b485bb157e12ec5764991281fa`
