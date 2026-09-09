# ruff: noqa: E501
"""PostgreSQL persistence for immutable financial posting evidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.finance import Account, AccountMapping, FinancialPeriod, JournalEntry, JournalLine, ReconciliationException, SupplierInvoice


class DurableFinancialStore:
    """Writes financial facts and their tenant-scoped outbox events atomically."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def save_account(self, account: Account, trace_id: str) -> None:
        self._commit(account.tenant_id, "AccountChanged", account.account_id, trace_id, lambda cursor: self._upsert_account(cursor, account))

    def save_mapping(self, mapping: AccountMapping, trace_id: str) -> None:
        self._commit(mapping.tenant_id, "AccountMappingChanged", mapping.mapping_id, trace_id, lambda cursor: self._upsert_mapping(cursor, mapping))

    def save_period(self, period: FinancialPeriod, trace_id: str) -> None:
        self._commit(period.tenant_id, "FinancialPeriodChanged", period.period_id, trace_id, lambda cursor: self._upsert_period(cursor, period))

    def post_journal(self, journal: JournalEntry) -> None:
        self._validate_journal(journal)
        self._commit(journal.tenant_id, "JournalPosted", journal.journal_id, journal.trace_id, lambda cursor: self._insert_journal(cursor, journal))

    def save_invoice(self, invoice: SupplierInvoice, trace_id: str) -> None:
        self._commit(invoice.tenant_id, "SupplierInvoiceChanged", invoice.invoice_id, trace_id, lambda cursor: self._upsert_invoice(cursor, invoice))

    def report_reconciliation_exception(self, exception: ReconciliationException) -> None:
        self._commit(exception.tenant_id, "ReconciliationException", exception.exception_id, exception.trace_id, lambda cursor: self._upsert_exception(cursor, exception))

    def _commit(self, tenant_id: str, event_type: str, aggregate_id: str, trace_id: str, write: Callable[[psycopg.Cursor[Any]], bool]) -> None:
        event = DurableEvent(f"{event_type}-{tenant_id}-{aggregate_id}-{trace_id}", tenant_id, event_type, "v1", trace_id, {"aggregate_id": aggregate_id}, datetime.now(UTC))
        self._outbox.commit_business_event(event, write)

    @staticmethod
    def _upsert_account(cursor: psycopg.Cursor[Any], account: Account) -> bool:
        cursor.execute("INSERT INTO chart_of_accounts (tenant_id, account_id, code, name, account_type, currency, status) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, account_id) DO UPDATE SET code = EXCLUDED.code, name = EXCLUDED.name, account_type = EXCLUDED.account_type, currency = EXCLUDED.currency, status = EXCLUDED.status", (account.tenant_id, account.account_id, account.code, account.name, account.account_type, account.currency, account.status))
        return cursor.rowcount > 0

    @staticmethod
    def _upsert_mapping(cursor: psycopg.Cursor[Any], mapping: AccountMapping) -> bool:
        cursor.execute("INSERT INTO account_mappings (tenant_id, mapping_id, event_type, legal_entity_id, debit_account_id, credit_account_id) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, event_type, legal_entity_id) DO UPDATE SET mapping_id = EXCLUDED.mapping_id, debit_account_id = EXCLUDED.debit_account_id, credit_account_id = EXCLUDED.credit_account_id", (mapping.tenant_id, mapping.mapping_id, mapping.event_type, mapping.legal_entity_id, mapping.debit_account_id, mapping.credit_account_id))
        return cursor.rowcount > 0

    @staticmethod
    def _upsert_period(cursor: psycopg.Cursor[Any], period: FinancialPeriod) -> bool:
        cursor.execute("INSERT INTO financial_periods (tenant_id, period_id, legal_entity_id, starts_on, ends_on, status) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, period_id) DO UPDATE SET status = EXCLUDED.status", (period.tenant_id, period.period_id, period.legal_entity_id, period.starts_on, period.ends_on, period.status))
        return cursor.rowcount > 0

    @staticmethod
    def _insert_journal(cursor: psycopg.Cursor[Any], journal: JournalEntry) -> bool:
        lines = json.dumps([{"account_id": line.account_id, "debit": str(line.debit), "credit": str(line.credit)} for line in journal.lines])
        cursor.execute("INSERT INTO journal_entries (tenant_id, journal_id, legal_entity_id, period_id, currency, lines, source_type, source_id, trace_id, reversal_of_journal_id, posted_at) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, source_type, source_id) DO NOTHING", (journal.tenant_id, journal.journal_id, journal.legal_entity_id, journal.period_id, journal.currency, lines, journal.source_type, journal.source_id, journal.trace_id, journal.reversal_of_journal_id, journal.posted_at))
        return cursor.rowcount > 0

    @staticmethod
    def _upsert_invoice(cursor: psycopg.Cursor[Any], invoice: SupplierInvoice) -> bool:
        cursor.execute("INSERT INTO supplier_invoices (tenant_id, invoice_id, supplier_id, supplier_invoice_number, purchase_order_id, receipt_id, legal_entity_id, amount, currency, match_status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, invoice_id) DO UPDATE SET match_status = EXCLUDED.match_status", (invoice.tenant_id, invoice.invoice_id, invoice.supplier_id, invoice.supplier_invoice_number, invoice.purchase_order_id, invoice.receipt_id, invoice.legal_entity_id, invoice.amount, invoice.currency, invoice.status))
        return cursor.rowcount > 0

    @staticmethod
    def _upsert_exception(cursor: psycopg.Cursor[Any], exception: ReconciliationException) -> bool:
        cursor.execute("INSERT INTO reconciliation_exceptions (tenant_id, exception_id, journal_id, expected_reference, actual_reference, reason, trace_id, resolved, resolution_reference) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, exception_id) DO UPDATE SET resolved = EXCLUDED.resolved, resolution_reference = EXCLUDED.resolution_reference", (exception.tenant_id, exception.exception_id, exception.journal_id, exception.expected_reference, exception.actual_reference, exception.reason, exception.trace_id, exception.resolved, exception.resolution_reference))
        return cursor.rowcount > 0

    @staticmethod
    def _validate_journal(journal: JournalEntry) -> None:
        debits = sum((line.debit for line in journal.lines), Decimal())
        credits = sum((line.credit for line in journal.lines), Decimal())
        if not journal.lines or debits <= 0 or debits != credits:
            raise ValueError("journal debits and credits must balance exactly")
        if any(line.debit < 0 or line.credit < 0 or bool(line.debit) == bool(line.credit) for line in journal.lines):
            raise ValueError("each journal line must have one positive side")
