CREATE TABLE IF NOT EXISTS kvr_shifts (
  tenant_id TEXT NOT NULL,
  shift_id TEXT NOT NULL,
  cashier_id TEXT NOT NULL,
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  opening_float_cents INTEGER NOT NULL CHECK (opening_float_cents >= 0),
  closed_at TIMESTAMPTZ,
  declared_cash_cents INTEGER,
  recorded_cash_cents INTEGER,
  PRIMARY KEY (tenant_id, shift_id)
);

CREATE TABLE IF NOT EXISTS kvr_sales (
  tenant_id TEXT NOT NULL,
  sale_id TEXT NOT NULL,
  shift_id TEXT NOT NULL,
  cashier_id TEXT NOT NULL,
  subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
  total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
  payment_method TEXT NOT NULL CHECK (payment_method IN ('cash', 'card', 'qr', 'split')),
  tendered_cents INTEGER NOT NULL CHECK (tendered_cents >= 0),
  change_cents INTEGER NOT NULL CHECK (change_cents >= 0),
  status TEXT NOT NULL CHECK (status IN ('completed', 'returned')),
  idempotency_key TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, sale_id),
  UNIQUE (tenant_id, idempotency_key),
  FOREIGN KEY (tenant_id, shift_id) REFERENCES kvr_shifts (tenant_id, shift_id)
);

CREATE TABLE IF NOT EXISTS kvr_sale_lines (
  tenant_id TEXT NOT NULL,
  sale_id TEXT NOT NULL,
  line_number INTEGER NOT NULL CHECK (line_number > 0),
  product_id TEXT NOT NULL,
  product_name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  line_total_cents INTEGER NOT NULL CHECK (line_total_cents >= 0),
  PRIMARY KEY (tenant_id, sale_id, line_number),
  FOREIGN KEY (tenant_id, sale_id) REFERENCES kvr_sales (tenant_id, sale_id)
);

CREATE TABLE IF NOT EXISTS kvr_inventory_movements (
  tenant_id TEXT NOT NULL,
  movement_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  quantity_delta INTEGER NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('sale', 'return', 'receipt', 'adjustment', 'reversal')),
  reference_id TEXT NOT NULL,
  reversal_of_movement_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, movement_id)
);

CREATE TABLE IF NOT EXISTS kvr_receipts (
  tenant_id TEXT NOT NULL,
  receipt_number TEXT NOT NULL,
  sale_id TEXT NOT NULL,
  business_name TEXT NOT NULL,
  currency_code TEXT NOT NULL,
  total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, receipt_number),
  UNIQUE (tenant_id, sale_id),
  FOREIGN KEY (tenant_id, sale_id) REFERENCES kvr_sales (tenant_id, sale_id)
);
