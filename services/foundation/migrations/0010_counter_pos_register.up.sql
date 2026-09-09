BEGIN;

CREATE TABLE register_shifts (
  tenant_id TEXT NOT NULL,
  shift_id TEXT NOT NULL,
  store_id TEXT NOT NULL,
  register_id TEXT NOT NULL,
  opened_by TEXT NOT NULL,
  opening_float NUMERIC(18, 4) NOT NULL CHECK (opening_float >= 0),
  opened_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
  declared_cash NUMERIC(18, 4),
  recorded_cash NUMERIC(18, 4),
  variance NUMERIC(18, 4),
  closed_by TEXT,
  closed_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, shift_id)
);
CREATE INDEX register_shifts_register_idx ON register_shifts (tenant_id, register_id, opened_at DESC);

CREATE TABLE pos_sales (
  tenant_id TEXT NOT NULL,
  sale_id TEXT NOT NULL,
  shift_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  lines JSONB NOT NULL,
  total_amount NUMERIC(18, 4) NOT NULL CHECK (total_amount >= 0),
  currency TEXT NOT NULL,
  tender_type TEXT NOT NULL CHECK (tender_type IN ('cash', 'card', 'other')),
  payment_transaction_id TEXT NOT NULL,
  inventory_movement_ids JSONB NOT NULL,
  receipt_id TEXT NOT NULL,
  connectivity_state TEXT NOT NULL CHECK (connectivity_state IN ('online', 'offline')),
  sold_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, sale_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, shift_id) REFERENCES register_shifts (tenant_id, shift_id) ON DELETE RESTRICT
);
CREATE INDEX pos_sales_shift_idx ON pos_sales (tenant_id, shift_id);

CREATE TABLE return_authorizations (
  tenant_id TEXT NOT NULL,
  authorization_id TEXT NOT NULL,
  sale_id TEXT NOT NULL,
  authorized_by TEXT NOT NULL,
  granted_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, authorization_id),
  FOREIGN KEY (tenant_id, sale_id) REFERENCES pos_sales (tenant_id, sale_id) ON DELETE RESTRICT
);
CREATE INDEX return_authorizations_sale_idx ON return_authorizations (tenant_id, sale_id, granted_at DESC);

CREATE TABLE pos_returns (
  tenant_id TEXT NOT NULL,
  return_id TEXT NOT NULL,
  original_sale_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  lines JSONB NOT NULL,
  total_amount NUMERIC(18, 4) NOT NULL CHECK (total_amount >= 0),
  tender_type TEXT NOT NULL CHECK (tender_type IN ('cash', 'card', 'other')),
  refund_transaction_id TEXT NOT NULL,
  inventory_movement_ids JSONB NOT NULL,
  reason TEXT NOT NULL,
  returned_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, return_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, original_sale_id) REFERENCES pos_sales (tenant_id, sale_id) ON DELETE RESTRICT
);
CREATE INDEX pos_returns_sale_idx ON pos_returns (tenant_id, original_sale_id);

CREATE OR REPLACE FUNCTION reject_pos_fact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'pos sale, return, and authorization records are append-only'; END;
$$;
CREATE TRIGGER pos_sales_immutable BEFORE UPDATE OR DELETE ON pos_sales
  FOR EACH ROW EXECUTE FUNCTION reject_pos_fact_mutation();
CREATE TRIGGER return_authorizations_immutable BEFORE UPDATE OR DELETE ON return_authorizations
  FOR EACH ROW EXECUTE FUNCTION reject_pos_fact_mutation();
CREATE TRIGGER pos_returns_immutable BEFORE UPDATE OR DELETE ON pos_returns
  FOR EACH ROW EXECUTE FUNCTION reject_pos_fact_mutation();

COMMIT;
