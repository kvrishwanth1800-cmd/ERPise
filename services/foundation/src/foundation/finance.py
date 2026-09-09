"""Controlled, append-only financial posting and reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class FinanceValidationError(ValueError):
    """Raised when a financial control is violated."""


@dataclass(frozen=True)
class Account:
    account_id: str
    tenant_id: str
    code: str
    name: str
    account_type: str
    currency: str
    status: str = "active"


@dataclass(frozen=True)
class AccountMapping:
    mapping_id: str
    tenant_id: str
    event_type: str
    legal_entity_id: str
    debit_account_id: str
    credit_account_id: str


@dataclass(frozen=True)
class FinancialPeriod:
    period_id: str
    tenant_id: str
    legal_entity_id: str
    starts_on: date
    ends_on: date
    status: str = "open"


@dataclass(frozen=True)
class JournalLine:
    account_id: str
    debit: Decimal = Decimal()
    credit: Decimal = Decimal()


@dataclass(frozen=True)
class JournalEntry:
    journal_id: str
    tenant_id: str
    legal_entity_id: str
    period_id: str
    currency: str
    lines: tuple[JournalLine, ...]
    source_type: str
    source_id: str
    trace_id: str
    posted_at: datetime
    reversal_of_journal_id: str | None = None


@dataclass(frozen=True)
class SupplierInvoice:
    invoice_id: str
    tenant_id: str
    supplier_id: str
    supplier_invoice_number: str
    purchase_order_id: str
    receipt_id: str
    legal_entity_id: str
    amount: Decimal
    currency: str
    purchase_order_amount: Decimal
    receipt_amount: Decimal
    status: str = "pending"


@dataclass(frozen=True)
class MatchOutcome:
    invoice_id: str
    status: str
    exception_reason: str | None = None


@dataclass(frozen=True)
class ReconciliationException:
    exception_id: str
    tenant_id: str
    journal_id: str
    expected_reference: str
    actual_reference: str | None
    reason: str
    trace_id: str
    resolved: bool = False
    resolution_reference: str | None = None


@dataclass(frozen=True)
class FinanceEvent:
    event_type: str
    tenant_id: str
    aggregate_id: str


class FinancialPostingService:
    """Posts balanced, source-traceable journals and controlled AP evidence."""

    _account_types = frozenset({"asset", "liability", "equity", "revenue", "expense"})
    _source_types = frozenset({"sale", "payment", "refund", "procurement", "receiving", "inventory_movement", "adjustment"})

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._accounts: dict[tuple[str, str], Account] = {}
        self._mappings: dict[tuple[str, str, str], AccountMapping] = {}
        self._periods: dict[tuple[str, str], FinancialPeriod] = {}
        self._journals: dict[tuple[str, str], JournalEntry] = {}
        self._sources: set[tuple[str, str, str]] = set()
        self._invoices: dict[tuple[str, str], SupplierInvoice] = {}
        self._invoice_keys: set[tuple[str, str, str]] = set()
        self.outbox: list[FinanceEvent] = []

    def create_account(self, principal_id: str, session_id: str, scope: ScopeContext, account: Account, trace_id: str) -> Account:
        self._authorize(principal_id, session_id, scope)
        if account.tenant_id != scope.tenant_id or not account.code or account.account_type not in self._account_types or not account.currency or account.status not in {"active", "inactive"}:
            raise FinanceValidationError("account is invalid for this tenant")
        key = (scope.tenant_id, account.account_id)
        if key in self._accounts or any(item.tenant_id == scope.tenant_id and item.code == account.code for item in self._accounts.values()):
            raise FinanceValidationError("account id or code already exists in this tenant")
        self._accounts[key] = account
        self._record(principal_id, "account.create", account.account_id, trace_id, "AccountChanged")
        return account

    def define_mapping(self, principal_id: str, session_id: str, scope: ScopeContext, mapping: AccountMapping, trace_id: str) -> AccountMapping:
        self._authorize(principal_id, session_id, scope)
        if mapping.tenant_id != scope.tenant_id or not mapping.event_type or not mapping.legal_entity_id:
            raise FinanceValidationError("account mapping is invalid for this tenant")
        if self._account(scope, mapping.debit_account_id).status != "active" or self._account(scope, mapping.credit_account_id).status != "active":
            raise FinanceValidationError("account mapping requires active accounts")
        self._mappings[(scope.tenant_id, mapping.event_type, mapping.legal_entity_id)] = mapping
        self._record(principal_id, "mapping.define", mapping.mapping_id, trace_id, "AccountMappingChanged")
        return mapping

    def open_period(self, principal_id: str, session_id: str, scope: ScopeContext, period: FinancialPeriod, trace_id: str) -> FinancialPeriod:
        self._authorize(principal_id, session_id, scope)
        if period.tenant_id != scope.tenant_id or period.starts_on > period.ends_on or period.status != "open":
            raise FinanceValidationError("financial period is invalid")
        key = (scope.tenant_id, period.period_id)
        if key in self._periods:
            raise FinanceValidationError("financial period already exists")
        self._periods[key] = period
        self._record(principal_id, "period.open", period.period_id, trace_id, "FinancialPeriodChanged")
        return period

    def close_period(self, principal_id: str, session_id: str, scope: ScopeContext, period_id: str, trace_id: str) -> FinancialPeriod:
        self._authorize(principal_id, session_id, scope)
        period = self._period(scope, period_id)
        if period.status != "open":
            raise FinanceValidationError("only an open financial period can be closed")
        closed = replace(period, status="closed")
        self._periods[(scope.tenant_id, period_id)] = closed
        self._record(principal_id, "period.close", period_id, trace_id, "FinancialPeriodChanged")
        return closed

    def post_journal(self, principal_id: str, session_id: str, scope: ScopeContext, journal: JournalEntry) -> JournalEntry:
        self._authorize(principal_id, session_id, scope)
        self._validate_journal(scope, journal)
        key = (scope.tenant_id, journal.journal_id)
        source_key = (scope.tenant_id, journal.source_type, journal.source_id)
        if key in self._journals or source_key in self._sources:
            raise FinanceValidationError("journal or source transaction has already posted")
        self._journals[key] = journal
        self._sources.add(source_key)
        self._record(principal_id, "journal.post", journal.journal_id, journal.trace_id, "JournalPosted")
        return journal

    def post_from_mapping(self, principal_id: str, session_id: str, scope: ScopeContext, journal_id: str, event_type: str, source_id: str, legal_entity_id: str, period_id: str, currency: str, amount: Decimal, trace_id: str, occurred_at: datetime) -> JournalEntry:
        mapping = self._mappings.get((scope.tenant_id, event_type, legal_entity_id))
        if mapping is None:
            raise FinanceValidationError("no account mapping exists for this posting event")
        if amount <= 0:
            raise FinanceValidationError("financial posting amount must be positive")
        return self.post_journal(principal_id, session_id, scope, JournalEntry(journal_id, scope.tenant_id, legal_entity_id, period_id, currency, (JournalLine(mapping.debit_account_id, debit=amount), JournalLine(mapping.credit_account_id, credit=amount)), event_type, source_id, trace_id, occurred_at))

    def reverse_journal(self, principal_id: str, session_id: str, scope: ScopeContext, journal_id: str, reversal_id: str, reason: str, trace_id: str, occurred_at: datetime) -> JournalEntry:
        self._authorize(principal_id, session_id, scope)
        original = self._journal(scope, journal_id)
        if not reason or any(item.reversal_of_journal_id == journal_id for item in self._journals.values()):
            raise FinanceValidationError("a posted journal requires one linked reversal with a reason")
        reversal = JournalEntry(reversal_id, scope.tenant_id, original.legal_entity_id, original.period_id, original.currency, tuple(JournalLine(line.account_id, line.credit, line.debit) for line in original.lines), "adjustment", f"reversal:{journal_id}", trace_id, occurred_at, journal_id)
        return self.post_journal(principal_id, session_id, scope, reversal)

    def capture_invoice(self, principal_id: str, session_id: str, scope: ScopeContext, invoice: SupplierInvoice, trace_id: str) -> SupplierInvoice:
        self._authorize(principal_id, session_id, scope)
        key = (scope.tenant_id, invoice.invoice_id)
        duplicate_key = (scope.tenant_id, invoice.supplier_id, invoice.supplier_invoice_number)
        if invoice.tenant_id != scope.tenant_id or invoice.amount <= 0 or not invoice.currency or key in self._invoices or duplicate_key in self._invoice_keys:
            raise FinanceValidationError("supplier invoice is invalid or duplicates an eligible payable")
        self._invoices[key] = invoice
        self._invoice_keys.add(duplicate_key)
        self._record(principal_id, "invoice.capture", invoice.invoice_id, trace_id, "SupplierInvoiceChanged")
        return invoice

    def match_invoice(self, principal_id: str, session_id: str, scope: ScopeContext, invoice_id: str, trace_id: str) -> MatchOutcome:
        self._authorize(principal_id, session_id, scope)
        invoice = self._invoices[(scope.tenant_id, invoice_id)]
        if invoice.amount == invoice.purchase_order_amount == invoice.receipt_amount:
            outcome = MatchOutcome(invoice_id, "matched")
            self._invoices[(scope.tenant_id, invoice_id)] = replace(invoice, status="matched")
        else:
            outcome = MatchOutcome(invoice_id, "exception", "purchase order, receipt, and supplier invoice amounts differ")
            self._invoices[(scope.tenant_id, invoice_id)] = replace(invoice, status="exception")
        self._record(principal_id, "invoice.match", invoice_id, trace_id, "ThreeWayMatchReported")
        return outcome

    def journal(self, scope: ScopeContext, journal_id: str) -> JournalEntry | None:
        return self._journals.get((scope.tenant_id, journal_id))

    def _validate_journal(self, scope: ScopeContext, journal: JournalEntry) -> None:
        period = self._period(scope, journal.period_id)
        if journal.tenant_id != scope.tenant_id or journal.legal_entity_id != period.legal_entity_id or period.status != "open":
            raise FinanceValidationError("financial period is closed or outside the legal entity scope")
        if journal.source_type not in self._source_types or not journal.source_id or not journal.lines or not journal.currency or journal.posted_at.tzinfo is None:
            raise FinanceValidationError("journal source, lines, currency, or timestamp is invalid")
        debits = sum((line.debit for line in journal.lines), Decimal())
        credits = sum((line.credit for line in journal.lines), Decimal())
        if debits <= 0 or credits <= 0 or debits != credits:
            raise FinanceValidationError("journal debits and credits must balance exactly")
        for line in journal.lines:
            if line.debit < 0 or line.credit < 0 or bool(line.debit) == bool(line.credit) or self._account(scope, line.account_id).currency != journal.currency:
                raise FinanceValidationError("journal line is invalid for its account or currency")

    def _period(self, scope: ScopeContext, period_id: str) -> FinancialPeriod:
        try:
            return self._periods[(scope.tenant_id, period_id)]
        except KeyError as error:
            raise FinanceValidationError("financial period is not visible in this tenant") from error

    def _journal(self, scope: ScopeContext, journal_id: str) -> JournalEntry:
        try:
            return self._journals[(scope.tenant_id, journal_id)]
        except KeyError as error:
            raise FinanceValidationError("journal is not visible in this tenant") from error

    def _account(self, scope: ScopeContext, account_id: str) -> Account:
        try:
            return self._accounts[(scope.tenant_id, account_id)]
        except KeyError as error:
            raise FinanceValidationError("account is not visible in this tenant") from error

    def _authorize(self, principal_id: str, session_id: str, scope: ScopeContext) -> None:
        self._authorization.authorize(principal_id, session_id, scope, "finance.write")

    def _record(self, actor_id: str, source: str, record_id: str, trace_id: str, event_type: str) -> None:
        self._audit.record(actor_id, "finance.write", source, record_id, "finance", trace_id, "accepted")
        self.outbox.append(FinanceEvent(event_type, "", record_id))


class ReconciliationService:
    """Retains reconciliation exceptions and their immutable resolution evidence."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder, posting: FinancialPostingService) -> None:
        self._authorization = authorization
        self._audit = audit
        self._posting = posting
        self._exceptions: dict[tuple[str, str], ReconciliationException] = {}
        self.outbox: list[FinanceEvent] = []

    def report_exception(self, principal_id: str, session_id: str, scope: ScopeContext, exception: ReconciliationException) -> ReconciliationException:
        self._authorization.authorize(principal_id, session_id, scope, "finance.write")
        if exception.tenant_id != scope.tenant_id or not exception.reason or self._posting.journal(scope, exception.journal_id) is None:
            raise FinanceValidationError("reconciliation exception is invalid")
        key = (scope.tenant_id, exception.exception_id)
        if key in self._exceptions:
            return self._exceptions[key]
        self._exceptions[key] = exception
        self._audit.record(principal_id, "finance.write", "reconciliation.report", exception.exception_id, "finance", exception.trace_id, "accepted")
        self.outbox.append(FinanceEvent("ReconciliationException", scope.tenant_id, exception.exception_id))
        return exception

    def resolve_exception(self, principal_id: str, session_id: str, scope: ScopeContext, exception_id: str, resolution_reference: str, trace_id: str) -> ReconciliationException:
        self._authorization.authorize(principal_id, session_id, scope, "finance.write")
        item = self._exceptions[(scope.tenant_id, exception_id)]
        if item.resolved or not resolution_reference:
            raise FinanceValidationError("reconciliation exception is already resolved or lacks evidence")
        resolved = replace(item, resolved=True, resolution_reference=resolution_reference)
        self._exceptions[(scope.tenant_id, exception_id)] = resolved
        self._audit.record(principal_id, "finance.write", "reconciliation.resolve", exception_id, "finance", trace_id, "accepted")
        return resolved
