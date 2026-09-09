BEGIN;

CREATE TABLE products (
  product_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) > 0),
  unit_of_measure TEXT NOT NULL CHECK (length(unit_of_measure) > 0),
  lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('draft', 'active', 'discontinued')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, product_id)
);

CREATE TABLE product_variants (
  tenant_id TEXT NOT NULL,
  variant_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, variant_id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT
);

CREATE TABLE product_identifiers (
  tenant_id TEXT NOT NULL,
  identifier TEXT NOT NULL CHECK (length(identifier) > 0),
  product_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, identifier),
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT
);

CREATE INDEX products_tenant_lifecycle_idx ON products (tenant_id, lifecycle_status);
CREATE INDEX product_identifiers_product_idx ON product_identifiers (tenant_id, product_id);

COMMIT;
