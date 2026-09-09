BEGIN;

CREATE TABLE chart_of_accounts (
    tenant_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
    currency TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
    PRIMARY KEY (tenant_id, account_id),
    UNIQUE (tenant_id, code)
);
CREATE TABLE financial_periods (
    tenant_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    legal_entity_id TEXT NOT NULL,
    starts_on DATE NOT NULL,
    ends_on DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    PRIMARY KEY (tenant_id, period_id),
    CHECK (starts_on <= ends_on)
);
CREATE TABLE journal_entries (
    tenant_id TEXT NOT NULL,
    journal_id TEXT NOT NULL,
    legal_entity_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    lines JSONB NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('sale', 'payment', 'refund', 'procurement', 'receiving', 'inventory_movement', 'adjustment')),
    source_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    reversal_of_journal_id TEXT,
    posted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, journal_id),
    UNIQUE (tenant_id, source_type, source_id)
);
CREATE INDEX journal_entries_period_scope_idx ON journal_entries (tenant_id, legal_entity_id, period_id);
CREATE TABLE supplier_invoices (
    tenant_id TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    supplier_invoice_number TEXT NOT NULL,
    purchase_order_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    legal_entity_id TEXT NOT NULL,
    amount NUMERIC NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL,
    match_status TEXT NOT NULL CHECK (match_status IN ('pending', 'matched', 'exception')),
    PRIMARY KEY (tenant_id, invoice_id),
    UNIQUE (tenant_id, supplier_id, supplier_invoice_number)
);
CREATE TABLE reconciliation_exceptions (
    tenant_id TEXT NOT NULL,
    exception_id TEXT NOT NULL,
    journal_id TEXT NOT NULL,
    expected_reference TEXT NOT NULL,
    actual_reference TEXT,
    reason TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    resolved BOOLEAN NOT NULL DEFAULT false,
    resolution_reference TEXT,
    PRIMARY KEY (tenant_id, exception_id)
);
CREATE INDEX reconciliation_exceptions_scope_idx ON reconciliation_exceptions (tenant_id, journal_id, resolved);
COMMIT;
