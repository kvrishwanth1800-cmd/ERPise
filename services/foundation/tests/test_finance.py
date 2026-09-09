from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.audit import AuditRecorder
from foundation.finance import Account, AccountMapping, FinanceValidationError, FinancialPeriod, FinancialPostingService, JournalEntry, JournalLine, ReconciliationException, ReconciliationService, SupplierInvoice
from foundation.organization import ScopeContext

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def service() -> tuple[FinancialPostingService, ReconciliationService]:
    access = AuthorizationService(SessionRevocationService())
    access.grant(PermissionGrant("controller", "tenant-a", "finance.write"))
    posting = FinancialPostingService(access, AuditRecorder())
    reconciliation = ReconciliationService(access, AuditRecorder(), posting)
    for account in (Account("ar", "tenant-a", "1100", "Accounts receivable", "asset", "USD"), Account("revenue", "tenant-a", "4000", "Sales", "revenue", "USD"), Account("ap", "tenant-a", "2000", "Accounts payable", "liability", "USD"), Account("inventory", "tenant-a", "1300", "Inventory", "asset", "USD")):
        posting.create_account("controller", "session", scope(), account, "account")
    posting.open_period("controller", "session", scope(), FinancialPeriod("p1", "tenant-a", "entity-a", date(2026, 9, 1), date(2026, 9, 30)), "period")
    posting.define_mapping("controller", "session", scope(), AccountMapping("sales", "tenant-a", "sale", "entity-a", "ar", "revenue"), "mapping")
    return posting, reconciliation


def test_mapped_sale_posts_exact_balanced_ar_and_revenue_once() -> None:
    posting, _ = service()
    journal = posting.post_from_mapping("controller", "session", scope(), "j1", "sale", "order-1", "entity-a", "p1", "USD", Decimal("12.34"), "trace-1", NOW)
    assert sum((line.debit for line in journal.lines), Decimal()) == Decimal("12.34")
    assert sum((line.credit for line in journal.lines), Decimal()) == Decimal("12.34")
    with pytest.raises(FinanceValidationError, match="already posted"):
        posting.post_from_mapping("controller", "session", scope(), "j2", "sale", "order-1", "entity-a", "p1", "USD", Decimal("12.34"), "trace-2", NOW)


def test_journals_require_exact_balance_and_closed_period_rejects_posting() -> None:
    posting, _ = service()
    bad = JournalEntry("bad", "tenant-a", "entity-a", "p1", "USD", (JournalLine("ar", debit=Decimal("10")), JournalLine("revenue", credit=Decimal("9.99"))), "sale", "bad-order", "trace", NOW)
    with pytest.raises(FinanceValidationError, match="balance exactly"):
        posting.post_journal("controller", "session", scope(), bad)
    posting.close_period("controller", "session", scope(), "p1", "close")
    with pytest.raises(FinanceValidationError, match="closed"):
        posting.post_from_mapping("controller", "session", scope(), "closed", "sale", "closed-order", "entity-a", "p1", "USD", Decimal("1"), "trace", NOW)


def test_correction_uses_linked_reversal_and_reconciliation_evidence() -> None:
    posting, reconciliation = service()
    posting.post_from_mapping("controller", "session", scope(), "j1", "sale", "order-1", "entity-a", "p1", "USD", Decimal("5"), "trace", NOW)
    reversal = posting.reverse_journal("controller", "session", scope(), "j1", "j1-reversal", "customer return", "trace-r", NOW)
    assert reversal.reversal_of_journal_id == "j1"
    with pytest.raises(FinanceValidationError, match="linked reversal"):
        posting.reverse_journal("controller", "session", scope(), "j1", "another", "again", "trace-r", NOW)
    reported = reconciliation.report_exception("controller", "session", scope(), ReconciliationException("rec-1", "tenant-a", "j1", "settlement-1", None, "settlement missing", "trace-rec"))
    assert reconciliation.resolve_exception("controller", "session", scope(), reported.exception_id, "bank-statement-1", "trace-resolve").resolved


def test_duplicate_invoice_and_three_way_match_exception_are_controlled() -> None:
    posting, _ = service()
    invoice = SupplierInvoice("invoice-1", "tenant-a", "supplier-a", "INV-7", "po-1", "receipt-1", "entity-a", Decimal("10.00"), "USD", Decimal("10.00"), Decimal("9.00"))
    posting.capture_invoice("controller", "session", scope(), invoice, "invoice")
    assert posting.match_invoice("controller", "session", scope(), "invoice-1", "match").status == "exception"
    with pytest.raises(FinanceValidationError, match="duplicates"):
        posting.capture_invoice("controller", "session", scope(), SupplierInvoice("invoice-2", "tenant-a", "supplier-a", "INV-7", "po-1", "receipt-1", "entity-a", Decimal("10.00"), "USD", Decimal("10.00"), Decimal("10.00")), "duplicate")
