BEGIN;

CREATE TABLE price_entries (
  price_entry_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  price_list_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  store_id TEXT,
  channel_id TEXT,
  segment_id TEXT,
  currency TEXT NOT NULL,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount >= 0),
  effective_from TIMESTAMPTZ NOT NULL,
  effective_until TIMESTAMPTZ,
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT,
  CHECK (effective_until IS NULL OR effective_until > effective_from)
);

CREATE TABLE promotions (
  tenant_id TEXT NOT NULL,
  promotion_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  store_id TEXT,
  channel_id TEXT,
  segment_id TEXT,
  currency TEXT NOT NULL,
  discount_percent NUMERIC(5, 2) NOT NULL CHECK (discount_percent > 0 AND discount_percent <= 100),
  priority INTEGER NOT NULL,
  stackable BOOLEAN NOT NULL,
  coupon_code TEXT,
  effective_from TIMESTAMPTZ NOT NULL,
  effective_until TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, promotion_id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT,
  CHECK (effective_until IS NULL OR effective_until > effective_from)
);

CREATE TABLE coupon_commits (
  tenant_id TEXT NOT NULL,
  coupon_code TEXT NOT NULL,
  quote_key TEXT NOT NULL,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, coupon_code)
);

CREATE INDEX price_entries_lookup_idx ON price_entries (tenant_id, product_id, currency, effective_from);
CREATE INDEX promotions_lookup_idx ON promotions (tenant_id, product_id, currency, effective_from);

COMMIT;
