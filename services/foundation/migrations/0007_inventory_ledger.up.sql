BEGIN;

CREATE TABLE inventory_movements (
  tenant_id TEXT NOT NULL,
  movement_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  location_id TEXT NOT NULL,
  quantity_type TEXT NOT NULL CHECK (quantity_type IN ('physical', 'reserved', 'available', 'quarantined', 'damaged', 'expired', 'in_transit', 'incoming')),
  quantity_delta NUMERIC(18, 4) NOT NULL CHECK (quantity_delta <> 0),
  reason TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  reversal_of_movement_id TEXT,
  PRIMARY KEY (tenant_id, movement_id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, product_id) ON DELETE RESTRICT,
  FOREIGN KEY (tenant_id, reversal_of_movement_id) REFERENCES inventory_movements (tenant_id, movement_id) ON DELETE RESTRICT
);

CREATE INDEX inventory_movements_position_idx ON inventory_movements (tenant_id, product_id, location_id, quantity_type, occurred_at, movement_id);

COMMIT;
