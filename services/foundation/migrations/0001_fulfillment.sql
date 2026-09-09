CREATE TABLE fulfillment_slots (
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity >= 0),
    reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0 AND reserved <= capacity),
    PRIMARY KEY (tenant_id, store_id, warehouse_id, slot_id)
);

CREATE TABLE fulfillment_promises (
    fulfillment_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    store_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('pickup', 'delivery')),
    slot_id TEXT NOT NULL,
    address TEXT,
    status TEXT NOT NULL,
    driver_id TEXT,
    pickup_confirmation TEXT,
    delivery_proof TEXT,
    follow_up TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE fulfillment_transitions (
    transition_id TEXT PRIMARY KEY,
    fulfillment_id TEXT NOT NULL REFERENCES fulfillment_promises(fulfillment_id),
    tenant_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fulfillment_outbox (
    event_id TEXT PRIMARY KEY,
    fulfillment_id TEXT NOT NULL REFERENCES fulfillment_promises(fulfillment_id),
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMPTZ
);

CREATE INDEX fulfillment_active_tenant_idx
    ON fulfillment_promises (tenant_id, status);
CREATE INDEX fulfillment_transition_lookup_idx
    ON fulfillment_transitions (fulfillment_id, created_at);

-- DOWN
DROP TABLE fulfillment_outbox;
DROP TABLE fulfillment_transitions;
DROP TABLE fulfillment_promises;
DROP TABLE fulfillment_slots;
