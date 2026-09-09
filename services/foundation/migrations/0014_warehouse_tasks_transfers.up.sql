BEGIN;

CREATE TABLE warehouse_tasks (
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    warehouse_id TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK (task_type IN ('putaway', 'picking', 'packing', 'internal_movement')),
    status TEXT NOT NULL CHECK (status IN ('open', 'assigned', 'in_progress', 'completed', 'cancelled')),
    assignee_id TEXT,
    claimed_by TEXT,
    details JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, task_id)
);
CREATE INDEX warehouse_tasks_scope_idx ON warehouse_tasks (tenant_id, warehouse_id, status);

CREATE TABLE warehouse_transfers (
    tenant_id TEXT NOT NULL,
    transfer_id TEXT NOT NULL,
    source_warehouse_id TEXT NOT NULL,
    source_bin_id TEXT NOT NULL,
    destination_warehouse_id TEXT NOT NULL,
    destination_bin_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft', 'in_transit', 'partially_received', 'received', 'cancelled')),
    lines JSONB NOT NULL,
    discrepancy_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, transfer_id),
    CHECK (source_warehouse_id <> destination_warehouse_id OR source_bin_id <> destination_bin_id)
);
CREATE INDEX warehouse_transfers_source_scope_idx ON warehouse_transfers (tenant_id, source_warehouse_id, status);
CREATE INDEX warehouse_transfers_destination_scope_idx ON warehouse_transfers (tenant_id, destination_warehouse_id, status);

COMMIT;
