BEGIN;

CREATE TABLE requisitions (
    tenant_id TEXT NOT NULL,
    requisition_id TEXT NOT NULL,
    requester_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    description TEXT NOT NULL,
    estimated_amount NUMERIC(18, 2) NOT NULL CHECK (estimated_amount > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved')),
    submitted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, requisition_id),
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE quotes (
    tenant_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    requisition_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    amount NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, quote_id),
    FOREIGN KEY (tenant_id, requisition_id) REFERENCES requisitions (tenant_id, requisition_id) ON DELETE RESTRICT
);

CREATE TABLE awards (
    tenant_id TEXT NOT NULL,
    award_id TEXT NOT NULL,
    requisition_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    awarded_amount NUMERIC(18, 2) NOT NULL CHECK (awarded_amount > 0),
    currency TEXT NOT NULL,
    compared_quote_ids JSONB NOT NULL,
    rationale TEXT NOT NULL,
    awarded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, award_id),
    FOREIGN KEY (tenant_id, requisition_id) REFERENCES requisitions (tenant_id, requisition_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION reject_procurement_fact_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'procurement evidence is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER awards_append_only_update
    BEFORE UPDATE ON awards
    FOR EACH ROW EXECUTE FUNCTION reject_procurement_fact_mutation();

CREATE TRIGGER awards_append_only_delete
    BEFORE DELETE ON awards
    FOR EACH ROW EXECUTE FUNCTION reject_procurement_fact_mutation();

CREATE TABLE purchase_orders (
    tenant_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    award_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    lines JSONB NOT NULL,
    total_amount NUMERIC(18, 2) NOT NULL CHECK (total_amount > 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('issued', 'acknowledged', 'partially_received', 'received', 'closed', 'cancelled')
    ),
    issued_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, po_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, award_id) REFERENCES awards (tenant_id, award_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_history (
    tenant_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    transition TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    detail TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, entry_id),
    UNIQUE (tenant_id, po_id, sequence),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_acknowledgments (
    tenant_id TEXT NOT NULL,
    acknowledgment_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    supplier_reference TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, acknowledgment_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_changes (
    tenant_id TEXT NOT NULL,
    change_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, change_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_asns (
    tenant_id TEXT NOT NULL,
    asn_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    line_id TEXT NOT NULL,
    quantity_received NUMERIC(18, 4) NOT NULL CHECK (quantity_received > 0),
    reason TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, asn_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_closures (
    tenant_id TEXT NOT NULL,
    closure_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    closed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, closure_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

CREATE TABLE purchase_order_cancellations (
    tenant_id TEXT NOT NULL,
    cancellation_id TEXT NOT NULL,
    po_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    cancelled_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, cancellation_id),
    FOREIGN KEY (tenant_id, po_id) REFERENCES purchase_orders (tenant_id, po_id) ON DELETE RESTRICT
);

COMMIT;
