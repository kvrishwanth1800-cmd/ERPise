from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext
from foundation.pricing import (
    CouponAlreadyCommittedError,
    PriceEntry,
    PriceScope,
    PricingEngine,
    PricingValidationError,
    Promotion,
    QuoteRequest,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def scope(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def engine() -> PricingEngine:
    authorization = AuthorizationService(SessionRevocationService())
    authorization.grant(PermissionGrant("merchandiser", "tenant-a", "pricing.write"))
    return PricingEngine(authorization, AuditRecorder())


def price(
    list_id: str,
    amount: str,
    pricing_scope: PriceScope,
    start: datetime = NOW - timedelta(days=1),
    end: datetime | None = None,
) -> PriceEntry:
    return PriceEntry(list_id, "tea", pricing_scope, Decimal(amount), start, end)


def promotion(
    promotion_id: str,
    percent: str,
    pricing_scope: PriceScope,
    priority: int = 1,
    stackable: bool = True,
    coupon: str | None = None,
) -> Promotion:
    return Promotion(
        promotion_id, "tea", pricing_scope, Decimal(percent), priority, stackable, NOW - timedelta(days=1), None, coupon
    )


def publish_price(subject: PricingEngine, entry: PriceEntry) -> None:
    subject.publish_price("merchandiser", "session", scope(), entry, "trace")


def publish_promotion(subject: PricingEngine, item: Promotion) -> None:
    subject.publish_promotion("merchandiser", "session", scope(), item, "trace")


def quote(subject: PricingEngine, pricing_scope: PriceScope, coupon: str | None = None):
    return subject.quote(scope(), QuoteRequest("tea", pricing_scope, NOW, coupon), "quote-trace")


def test_quote_selects_most_specific_price_with_stable_tie_break() -> None:
    subject = engine()
    publish_price(subject, price("z-list", "10.00", PriceScope(channel_id="web", currency="USD")))
    publish_price(subject, price("a-list", "11.00", PriceScope(channel_id="web", currency="USD")))
    publish_price(subject, price("store", "9.00", PriceScope(store_id="s1", channel_id="web", currency="USD")))

    result = quote(subject, PriceScope(store_id="s1", channel_id="web", currency="USD"))

    assert result.applied_price_list_id == "store"
    assert result.net_amount == Decimal("9.00")


def test_quote_applies_stackable_promotions_or_one_exclusive_promotion() -> None:
    subject = engine()
    pricing_scope = PriceScope(channel_id="web", currency="USD")
    publish_price(subject, price("base", "100.00", pricing_scope))
    publish_promotion(subject, promotion("ten", "10", pricing_scope, priority=1))
    publish_promotion(subject, promotion("five", "5", pricing_scope, priority=1))

    stacked = quote(subject, pricing_scope)
    assert stacked.net_amount == Decimal("85.50")
    assert tuple(item.promotion_id for item in stacked.discounts) == ("five", "ten")

    publish_promotion(subject, promotion("exclusive", "20", pricing_scope, priority=9, stackable=False))
    exclusive = quote(subject, pricing_scope)
    assert exclusive.net_amount == Decimal("80.00")
    assert tuple(item.promotion_id for item in exclusive.discounts) == ("exclusive",)


def test_quote_rounding_boundaries_and_currency_are_deterministic() -> None:
    subject = engine()
    pricing_scope = PriceScope(channel_id="web", currency="USD")
    publish_price(subject, price("base", "10.005", pricing_scope))
    publish_promotion(subject, promotion("third", "33.333", pricing_scope))

    result = quote(subject, pricing_scope)

    assert result.list_price == Decimal("10.01")
    assert result.net_amount == Decimal("6.67")
    assert result.tax_basis_amount == result.net_amount
    assert result.currency == "USD"


def test_price_rejects_overlapping_same_scope_and_boundary_end_is_valid() -> None:
    subject = engine()
    pricing_scope = PriceScope(channel_id="web", currency="USD")
    publish_price(subject, price("old", "10.00", pricing_scope, NOW - timedelta(days=2), NOW))
    publish_price(subject, price("new", "12.00", pricing_scope, NOW))

    assert quote(subject, pricing_scope).net_amount == Decimal("12.00")
    with pytest.raises(PricingValidationError, match="overlapping"):
        publish_price(subject, price("overlap", "11.00", pricing_scope, NOW - timedelta(hours=1)))


def test_tenant_isolation_coupon_idempotency_and_events() -> None:
    subject = engine()
    pricing_scope = PriceScope(channel_id="web", currency="USD")
    publish_price(subject, price("base", "10.00", pricing_scope))
    publish_promotion(subject, promotion("coupon", "10", pricing_scope, coupon="SAVE"))
    result = quote(subject, pricing_scope, "SAVE")
    subject.commit_coupon(scope(), "SAVE", result, "commit")
    subject.commit_coupon(scope(), "SAVE", result, "retry")

    with pytest.raises(CouponAlreadyCommittedError):
        subject.commit_coupon(scope(), "SAVE", result.__class__(
            "coffee", "USD", Decimal("1"), Decimal("0"), Decimal("1"), Decimal("1"), "base", ()
        ), "repeat")
    with pytest.raises(PricingValidationError, match="no applicable"):
        subject.quote(scope("tenant-b"), QuoteRequest("tea", pricing_scope, NOW), "tenant-b")
    assert [event.event_type for event in subject.outbox] == ["QuoteCalculated", "CouponCommitted"]


def test_unauthorized_pricing_change_is_denied() -> None:
    subject = engine()
    with pytest.raises(AuthorizationDeniedError):
        subject.publish_price("other", "session", scope(), price("base", "10.00", PriceScope(currency="USD")), "trace")
