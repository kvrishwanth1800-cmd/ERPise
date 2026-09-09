BEGIN;

ALTER TABLE reconciliation_exceptions
    DROP CONSTRAINT reconciliation_exceptions_journal_fk;
ALTER TABLE journal_entries
    DROP CONSTRAINT journal_entries_reversal_fk;
DROP TABLE account_mappings;

COMMIT;
