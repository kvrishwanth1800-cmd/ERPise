BEGIN;

CREATE TABLE assortments (
  tenant_id TEXT NOT NULL,
  assortment_id TEXT NOT NULL,
  store_id TEXT,
  channel_id TEXT,
  segment_id TEXT,
  effective_from TIMESTAMPTZ NOT NULL,
  effective_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, assortment_id),
  CHECK (store_id IS NOT NULL OR channel_id IS NOT NULL OR segment_id IS NOT NULL),
  CHECK (effective_until IS NULL OR effective_until > effective_from)
);

CREATE TABLE assortment_products (
  tenant_id TEXT NOT NULL,
  assortment_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  PRIMARY KEY (tenant_id, assortment_id, product_id),
  FOREIGN KEY (tenant_id, assortment_id)
    REFERENCES assortments (tenant_id, assortment_id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, product_id)
    REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT
);

CREATE INDEX assortments_scope_effective_idx
  ON assortments (tenant_id, store_id, channel_id, segment_id, effective_from);

COMMIT;
