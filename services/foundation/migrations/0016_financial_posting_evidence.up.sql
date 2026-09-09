BEGIN;

CREATE TABLE account_mappings (
    tenant_id TEXT NOT NULL,
    mapping_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    legal_entity_id TEXT NOT NULL,
    debit_account_id TEXT NOT NULL,
    credit_account_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, mapping_id),
    UNIQUE (tenant_id, event_type, legal_entity_id),
    FOREIGN KEY (tenant_id, debit_account_id) REFERENCES chart_of_accounts (tenant_id, account_id),
    FOREIGN KEY (tenant_id, credit_account_id) REFERENCES chart_of_accounts (tenant_id, account_id),
    CHECK (debit_account_id <> credit_account_id)
);

ALTER TABLE journal_entries
    ADD CONSTRAINT journal_entries_reversal_fk
    FOREIGN KEY (tenant_id, reversal_of_journal_id)
    REFERENCES journal_entries (tenant_id, journal_id);

ALTER TABLE reconciliation_exceptions
    ADD CONSTRAINT reconciliation_exceptions_journal_fk
    FOREIGN KEY (tenant_id, journal_id)
    REFERENCES journal_entries (tenant_id, journal_id);

COMMIT;
