# WO-5 context

## Isolated execution lane

**Owned files:** `services/foundation/src/foundation/audit.py`, `services/foundation/tests/test_audit.py`, and WO-5 execution evidence.

**Dependencies and fixed constraints:**

- WO-3 supplies trusted tenant and organization scope primitives.
- WO-4 supplies deny-by-default authorization and conflicting-duty controls. Future integration must authorize an eligible approver before calling the approval resolution boundary.
- Audit evidence is append-only. Corrections must create new evidence, never mutate existing records.

**Out of scope:** external identity provider configuration, persistent database adapters, APIs, deployment, and irreversible migrations.
