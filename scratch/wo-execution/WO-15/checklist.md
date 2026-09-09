# WO-15 checklist

- [x] Receipt evidence captures accepted quantity and optional cost, batch, serial, and expiry data.
- [x] Receipt processing records any expected-versus-accepted discrepancy before receipt completion evidence.
- [x] A tenant-scoped receipt ID returns one logical receipt result on retry and creates one inventory movement.
- [x] Receipt reversal preserves the original immutable receipt command and creates one corrective ledger movement.
- [x] Putaway request evidence is emitted with the receipt-recorded event.
- [x] Receiving authorization, tenant isolation, audit records, and append-only inventory corrections are covered by focused tests.
- [x] No receipt-persistence migration was added. WO-15 specifies the Foundation application-service receipt lifecycle, and the existing durable ledger migration has no documented receipt-evidence schema or integration contract to extend. Durable receipt storage requires a separately specified persistence boundary.

## Implementation commits

- `0188274112280d71d29527c3b7d565dbfc760d70`
- `2cdaf29b53a4f1f94ca3e981fae877d75a426cd9`
