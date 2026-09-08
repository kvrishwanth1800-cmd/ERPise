BEGIN;

CREATE TABLE payment_intents (
  tenant_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  order_reference TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
  currency TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, intent_id)
);

CREATE TABLE payment_transactions (
  tenant_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
  provider_reference TEXT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, transaction_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, intent_id) REFERENCES payment_intents (tenant_id, intent_id) ON DELETE RESTRICT
);
CREATE INDEX payment_transactions_intent_idx ON payment_transactions (tenant_id, intent_id);

CREATE TABLE payment_refunds (
  tenant_id TEXT NOT NULL,
  refund_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
  reason TEXT NOT NULL,
  refunded_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, refund_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, transaction_id) REFERENCES payment_transactions (tenant_id, transaction_id) ON DELETE RESTRICT
);
CREATE INDEX payment_refunds_transaction_idx ON payment_refunds (tenant_id, transaction_id);

CREATE TABLE payment_settlement_exceptions (
  tenant_id TEXT NOT NULL,
  exception_id TEXT NOT NULL,
  transaction_id TEXT NOT NULL,
  expected_amount NUMERIC(18, 4) NOT NULL,
  settled_amount NUMERIC(18, 4) NOT NULL,
  reason TEXT NOT NULL,
  reported_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, exception_id),
  CHECK (expected_amount <> settled_amount),
  FOREIGN KEY (tenant_id, transaction_id) REFERENCES payment_transactions (tenant_id, transaction_id) ON DELETE RESTRICT
);
CREATE INDEX payment_settlement_exceptions_transaction_idx ON payment_settlement_exceptions (tenant_id, transaction_id);

CREATE OR REPLACE FUNCTION reject_payment_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'payment records are append-only'; END;
$$;
CREATE TRIGGER payment_intents_immutable BEFORE UPDATE OR DELETE ON payment_intents
  FOR EACH ROW EXECUTE FUNCTION reject_payment_mutation();
CREATE TRIGGER payment_transactions_immutable BEFORE UPDATE OR DELETE ON payment_transactions
  FOR EACH ROW EXECUTE FUNCTION reject_payment_mutation();
CREATE TRIGGER payment_refunds_immutable BEFORE UPDATE OR DELETE ON payment_refunds
  FOR EACH ROW EXECUTE FUNCTION reject_payment_mutation();
CREATE TRIGGER payment_settlement_exceptions_immutable BEFORE UPDATE OR DELETE ON payment_settlement_exceptions
  FOR EACH ROW EXECUTE FUNCTION reject_payment_mutation();

COMMIT;
