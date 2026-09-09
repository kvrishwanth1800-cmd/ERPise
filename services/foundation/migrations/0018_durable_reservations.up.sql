BEGIN;

CREATE TABLE reservation_stock (
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    available_quantity BIGINT NOT NULL CHECK (available_quantity >= 0),
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, store_id, warehouse_id, product_id)
);

CREATE TABLE durable_reservations (
    reservation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity BIGINT NOT NULL CHECK (quantity > 0),
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'committed', 'released', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    version BIGINT NOT NULL DEFAULT 0,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX durable_reservations_active_expiry_idx
    ON durable_reservations (expires_at)
    WHERE status = 'reserved';
CREATE INDEX durable_reservations_scope_idx
    ON durable_reservations (tenant_id, store_id, warehouse_id, product_id, status);

COMMIT;
