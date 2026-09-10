CREATE TABLE IF NOT EXISTS demo_sessions (
  session_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_products (
  tenant_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  available INTEGER NOT NULL CHECK (available >= 0),
  PRIMARY KEY (tenant_id, product_id)
);
CREATE TABLE IF NOT EXISTS demo_customers (
  tenant_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  email TEXT NOT NULL,
  consented BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (tenant_id, customer_id)
);
CREATE TABLE IF NOT EXISTS demo_orders (
  tenant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  payment_id TEXT NOT NULL,
  fulfillment_status TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  PRIMARY KEY (tenant_id, order_id),
  UNIQUE (tenant_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS demo_outbox (
  event_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS demo_order_projection (
  tenant_id TEXT NOT NULL,
  order_id TEXT NOT NULL,
  fulfillment_status TEXT NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  PRIMARY KEY (tenant_id, order_id)
);
