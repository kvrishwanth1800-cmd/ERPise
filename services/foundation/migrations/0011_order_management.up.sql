BEGIN;

CREATE TABLE orders (
  tenant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  lines JSONB NOT NULL,
  total_amount NUMERIC(18, 4) NOT NULL CHECK (total_amount >= 0),
  currency TEXT NOT NULL,
  reservation_id TEXT NOT NULL,
  payment_transaction_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('placed', 'allocated', 'fulfilled', 'cancelled', 'returned', 'refunded')),
  placed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, order_id),
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX orders_customer_idx ON orders (tenant_id, customer_id, placed_at DESC);

CREATE TABLE order_allocations (
  tenant_id TEXT NOT NULL,
  allocation_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  line_id TEXT NOT NULL,
  location_id TEXT NOT NULL,
  quantity NUMERIC(18, 4) NOT NULL CHECK (quantity > 0),
  inventory_movement_id TEXT NOT NULL,
  allocated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, allocation_id),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_allocations_order_idx ON order_allocations (tenant_id, order_id);

CREATE TABLE order_substitutions (
  tenant_id TEXT NOT NULL,
  substitution_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  line_id TEXT NOT NULL,
  substitute_product_id TEXT NOT NULL,
  quantity NUMERIC(18, 4) NOT NULL CHECK (quantity > 0),
  reason TEXT NOT NULL,
  substituted_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, substitution_id),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_substitutions_order_idx ON order_substitutions (tenant_id, order_id);

CREATE TABLE order_cancellations (
  tenant_id TEXT NOT NULL,
  cancellation_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  cancelled_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, cancellation_id),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_cancellations_order_idx ON order_cancellations (tenant_id, order_id);

CREATE TABLE order_fulfillments (
  tenant_id TEXT NOT NULL,
  fulfillment_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  carrier_reference TEXT NOT NULL,
  fulfilled_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, fulfillment_id),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_fulfillments_order_idx ON order_fulfillments (tenant_id, order_id);

CREATE TABLE order_returns (
  tenant_id TEXT NOT NULL,
  return_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  lines JSONB NOT NULL,
  total_amount NUMERIC(18, 4) NOT NULL CHECK (total_amount >= 0),
  reason TEXT NOT NULL,
  returned_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, return_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_returns_order_idx ON order_returns (tenant_id, order_id);

CREATE TABLE order_refunds (
  tenant_id TEXT NOT NULL,
  refund_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  return_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount >= 0),
  payment_refund_id TEXT NOT NULL,
  refunded_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, refund_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id, return_id) REFERENCES order_returns (tenant_id, return_id) ON DELETE RESTRICT
);
CREATE INDEX order_refunds_order_idx ON order_refunds (tenant_id, order_id);

CREATE TABLE order_history (
  tenant_id TEXT NOT NULL,
  entry_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK (sequence > 0),
  transition TEXT NOT NULL,
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  detail TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, entry_id),
  UNIQUE (tenant_id, order_id, sequence),
  FOREIGN KEY (tenant_id, order_id) REFERENCES orders (tenant_id, order_id) ON DELETE RESTRICT
);
CREATE INDEX order_history_order_idx ON order_history (tenant_id, order_id, sequence);

CREATE OR REPLACE FUNCTION reject_order_fact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'order lifecycle and history records are append-only'; END;
$$;

CREATE OR REPLACE FUNCTION reject_order_line_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'committed orders are append-only';
  END IF;
  IF NEW.lines IS DISTINCT FROM OLD.lines
     OR NEW.total_amount IS DISTINCT FROM OLD.total_amount
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.reservation_id IS DISTINCT FROM OLD.reservation_id
     OR NEW.payment_transaction_id IS DISTINCT FROM OLD.payment_transaction_id
     OR NEW.placed_at IS DISTINCT FROM OLD.placed_at THEN
    RAISE EXCEPTION 'order lines, totals, and commercial references are append-only';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER orders_lines_immutable BEFORE UPDATE OR DELETE ON orders
  FOR EACH ROW EXECUTE FUNCTION reject_order_line_mutation();
CREATE TRIGGER order_allocations_immutable BEFORE UPDATE OR DELETE ON order_allocations
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_substitutions_immutable BEFORE UPDATE OR DELETE ON order_substitutions
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_cancellations_immutable BEFORE UPDATE OR DELETE ON order_cancellations
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_fulfillments_immutable BEFORE UPDATE OR DELETE ON order_fulfillments
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_returns_immutable BEFORE UPDATE OR DELETE ON order_returns
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_refunds_immutable BEFORE UPDATE OR DELETE ON order_refunds
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();
CREATE TRIGGER order_history_immutable BEFORE UPDATE OR DELETE ON order_history
  FOR EACH ROW EXECUTE FUNCTION reject_order_fact_mutation();

COMMIT;
