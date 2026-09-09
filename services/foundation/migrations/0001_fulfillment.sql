-- Reversible PostgreSQL storage for tenant-scoped fulfillment records.
CREATE TABLE fulfillment_promises (
  fulfillment_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  payment_id TEXT NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('pickup', 'delivery')),
  slot_id TEXT NOT NULL,
  address TEXT,
  status TEXT NOT NULL,
  driver_id TEXT,
  proof TEXT,
  follow_up TEXT,
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX fulfillment_promises_tenant_status_idx ON fulfillment_promises (tenant_id, status);

-- DOWN
DROP TABLE fulfillment_promises;
