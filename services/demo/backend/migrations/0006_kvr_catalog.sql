ALTER TABLE demo_products ADD COLUMN IF NOT EXISTS barcode TEXT;
ALTER TABLE demo_products ADD COLUMN IF NOT EXISTS unit TEXT NOT NULL DEFAULT 'each';
ALTER TABLE demo_products ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'active';
CREATE UNIQUE INDEX IF NOT EXISTS demo_products_tenant_barcode_unique
  ON demo_products (tenant_id, barcode) WHERE barcode IS NOT NULL;

CREATE TABLE IF NOT EXISTS kvr_product_imports (
  tenant_id TEXT NOT NULL,
  import_id TEXT NOT NULL,
  submitted_by TEXT NOT NULL,
  submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  row_count INTEGER NOT NULL CHECK (row_count > 0),
  status TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),
  error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
  PRIMARY KEY (tenant_id, import_id)
);
