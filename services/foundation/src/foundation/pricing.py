# ruff: noqa: E501, I001
"""Tenant-scoped deterministic pricing, promotions, and coupon commitment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext

MONEY_QUANTUM = Decimal("0.01")


class PricingValidationError(ValueError):
    """Raised when a price, promotion, or quote input is invalid."""


class CouponAlreadyCommittedError(ValueError):
    """Raised when a coupon is committed again for a different quote."""


@dataclass(frozen=True)
class PriceScope:
    store_id: str | None = None
    channel_id: str | None = None
    segment_id: str | None = None
    currency: str = ""

    @property
    def specificity(self) -> int:
        return sum(value is not None for value in self.values[:3])

    @property
    def values(self) -> tuple[str | None, str | None, str | None, str]:
        return self.store_id, self.channel_id, self.segment_id, self.currency

    def matches(self, query: PriceScope) -> bool:
        return (
            self.currency == query.currency
            and all(
                expected is None or expected == actual
                for expected, actual in zip(self.values[:3], query.values[:3], strict=True)
            )
        )


@dataclass(frozen=True)
class PriceEntry:
    price_list_id: str
    product_id: str
    scope: PriceScope
    amount: Decimal
    effective_from: datetime
    effective_until: datetime | None = None

    def effective_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (
            self.effective_until is None or at < self.effective_until
        )


@dataclass(frozen=True)
class Promotion:
    promotion_id: str
    product_id: str
    scope: PriceScope
    discount_percent: Decimal
    priority: int
    stackable: bool
    effective_from: datetime
    effective_until: datetime | None = None
    coupon_code: str | None = None

    def effective_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (
            self.effective_until is None or at < self.effective_until
        )


@dataclass(frozen=True)
class QuoteRequest:
    product_id: str
    scope: PriceScope
    at: datetime
    coupon_code: str | None = None


@dataclass(frozen=True)
class DiscountExplanation:
    promotion_id: str
    amount: Decimal
    reason: str


@dataclass(frozen=True)
class Quote:
    product_id: str
    currency: str
    list_price: Decimal
    discount_total: Decimal
    net_amount: Decimal
    tax_basis_amount: Decimal
    applied_price_list_id: str
    discounts: tuple[DiscountExplanation, ...]


@dataclass(frozen=True)
class PricingEvent:
    event_type: str
    quote: Quote | None
    coupon_code: str | None
    trace_id: str


class PricingEngine:
    """Central quote authority. Equal inputs always resolve to one quote."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._prices: dict[tuple[str, str, str, str], PriceEntry] = {}
        self._promotions: dict[tuple[str, str], Promotion] = {}
        self._coupon_commits: dict[tuple[str, str], str] = {}
        self.outbox: list[PricingEvent] = []

    def publish_price(self, principal_id: str, session_id: str, tenant_scope: ScopeContext, entry: PriceEntry, trace_id: str) -> None:
        self._authorization.authorize(principal_id, session_id, tenant_scope, "pricing.write")
        self._validate_price(entry)
        key = (tenant_scope.tenant_id, entry.price_list_id, entry.product_id, self._scope_key(entry.scope))
        if key in self._prices:
            raise PricingValidationError("duplicate price entry")
        self._reject_overlapping_price(tenant_scope.tenant_id, entry)
        self._prices[key] = entry
        self._audit.record(principal_id, "pricing", "price-entry", "published", "pricing.write", trace_id, "allowed")

    def publish_promotion(self, principal_id: str, session_id: str, tenant_scope: ScopeContext, promotion: Promotion, trace_id: str) -> None:
        self._authorization.authorize(principal_id, session_id, tenant_scope, "pricing.write")
        self._validate_promotion(promotion)
        key = tenant_scope.tenant_id, promotion.promotion_id
        if key in self._promotions:
            raise PricingValidationError("duplicate promotion")
        self._promotions[key] = promotion
        self._audit.record(principal_id, "pricing", "promotion", "published", "pricing.write", trace_id, "allowed")

    def quote(self, tenant_scope: ScopeContext, request: QuoteRequest, trace_id: str) -> Quote:
        self._validate_request(request)
        price = self._price_for(tenant_scope.tenant_id, request)
        if price is None:
            raise PricingValidationError("no applicable price")
        quote = self._calculate(tenant_scope.tenant_id, request, price)
        self.outbox.append(PricingEvent("QuoteCalculated", quote, None, trace_id))
        return quote

    def commit_coupon(self, tenant_scope: ScopeContext, coupon_code: str, quote: Quote, trace_id: str) -> None:
        key = tenant_scope.tenant_id, coupon_code
        quote_key = self._quote_key(quote)
        committed = self._coupon_commits.get(key)
        if committed is not None and committed != quote_key:
            raise CouponAlreadyCommittedError("coupon was already committed")
        if committed is None:
            self._coupon_commits[key] = quote_key
            self.outbox.append(PricingEvent("CouponCommitted", quote, coupon_code, trace_id))

    def _price_for(self, tenant_id: str, request: QuoteRequest) -> PriceEntry | None:
        candidates = [
            entry for (entry_tenant, _, product_id, _), entry in self._prices.items()
            if entry_tenant == tenant_id and product_id == request.product_id
            and entry.scope.matches(request.scope) and entry.effective_at(request.at)
        ]
        return min(candidates, key=self._price_sort_key) if candidates else None

    def _calculate(self, tenant_id: str, request: QuoteRequest, price: PriceEntry) -> Quote:
        candidates = [
            promotion for (promotion_tenant, _), promotion in self._promotions.items()
            if promotion_tenant == tenant_id and promotion.product_id == request.product_id
            and promotion.scope.matches(request.scope) and promotion.effective_at(request.at)
            and (promotion.coupon_code is None or promotion.coupon_code == request.coupon_code)
        ]
        ordered = sorted(candidates, key=self._promotion_sort_key)
        exclusive = next((promotion for promotion in ordered if not promotion.stackable), None)
        selected = (exclusive,) if exclusive is not None else tuple(ordered)
        amount = self._money(price.amount)
        discounts: list[DiscountExplanation] = []
        for promotion in selected:
            discount = self._money(amount * promotion.discount_percent / Decimal("100"))
            amount = self._money(amount - discount)
            discounts.append(DiscountExplanation(promotion.promotion_id, discount, "percentage discount"))
        discount_total = self._money(price.amount - amount)
        return Quote(request.product_id, request.scope.currency, self._money(price.amount), discount_total, amount, amount, price.price_list_id, tuple(discounts))

    @staticmethod
    def _price_sort_key(entry: PriceEntry) -> tuple[int, float, str]:
        return -entry.scope.specificity, -entry.effective_from.timestamp(), entry.price_list_id

    @staticmethod
    def _promotion_sort_key(promotion: Promotion) -> tuple[int, int, float, str]:
        return (-promotion.priority, -promotion.scope.specificity, -promotion.effective_from.timestamp(), promotion.promotion_id)

    def _reject_overlapping_price(self, tenant_id: str, candidate: PriceEntry) -> None:
        for (existing_tenant, _, product_id, _), existing in self._prices.items():
            if existing_tenant == tenant_id and product_id == candidate.product_id and existing.scope == candidate.scope and self._windows_overlap(existing, candidate):
                raise PricingValidationError("overlapping price entry for product and scope")

    @staticmethod
    def _windows_overlap(first: PriceEntry, second: PriceEntry) -> bool:
        first_end = first.effective_until or datetime.max.replace(tzinfo=UTC)
        second_end = second.effective_until or datetime.max.replace(tzinfo=UTC)
        return first.effective_from < second_end and second.effective_from < first_end

    @staticmethod
    def _scope_key(scope: PriceScope) -> str:
        return "|".join(value or "*" for value in scope.values)

    @staticmethod
    def _quote_key(quote: Quote) -> str:
        return f"{quote.product_id}|{quote.currency}|{quote.net_amount}|{quote.applied_price_list_id}"

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    @staticmethod
    def _validate_request(request: QuoteRequest) -> None:
        if not request.product_id or request.scope.currency == "":
            raise PricingValidationError("product and currency are required")
        if request.at.tzinfo is None:
            raise PricingValidationError("quote time must be timezone-aware")

    @staticmethod
    def _validate_price(entry: PriceEntry) -> None:
        if not entry.price_list_id or not entry.product_id or entry.scope.currency == "":
            raise PricingValidationError("price list, product, and currency are required")
        if entry.amount < Decimal("0"):
            raise PricingValidationError("price amount cannot be negative")
        if entry.effective_from.tzinfo is None or (entry.effective_until is not None and entry.effective_until.tzinfo is None):
            raise PricingValidationError("price times must be timezone-aware")
        if entry.effective_until is not None and entry.effective_until <= entry.effective_from:
            raise PricingValidationError("price end must be after start")

    @staticmethod
    def _validate_promotion(promotion: Promotion) -> None:
        if not promotion.promotion_id or not promotion.product_id or promotion.scope.currency == "":
            raise PricingValidationError("promotion, product, and currency are required")
        if promotion.discount_percent <= Decimal("0") or promotion.discount_percent > Decimal("100"):
            raise PricingValidationError("discount percent must be greater than zero and at most 100")
        if promotion.effective_from.tzinfo is None or (promotion.effective_until is not None and promotion.effective_until.tzinfo is None):
            raise PricingValidationError("promotion times must be timezone-aware")
        if promotion.effective_until is not None and promotion.effective_until <= promotion.effective_from:
            raise PricingValidationError("promotion end must be after start")


class QuoteApi:
    """Typed boundary for the central quote authority."""

    def __init__(self, engine: PricingEngine) -> None:
        self._engine = engine

    def calculate(self, tenant_scope: ScopeContext, request: QuoteRequest, trace_id: str) -> Quote:
        return self._engine.quote(tenant_scope, request, trace_id)
