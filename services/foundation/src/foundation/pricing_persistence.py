"""PostgreSQL persistence for pricing facts and coupon commitments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.pricing import CouponAlreadyCommittedError, PriceEntry, Promotion


class DurablePricingStore:
    """Atomically persists pricing changes and their versioned events."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._outbox = DurableOutboxStore(connection)

    def publish_price(self, tenant_id: str, entry: PriceEntry, trace_id: str) -> None:
        event = DurableEvent(f"PricePublished-{tenant_id}-{entry.price_list_id}-{entry.product_id}-{trace_id}", tenant_id, "PricePublished", "v1", trace_id, {"price_list_id": entry.price_list_id, "product_id": entry.product_id}, datetime.now(UTC))
        self._outbox.commit_business_event(event, lambda cursor: self._insert_price(cursor, tenant_id, entry))

    def publish_promotion(self, tenant_id: str, promotion: Promotion, trace_id: str) -> None:
        event = DurableEvent(f"PromotionPublished-{tenant_id}-{promotion.promotion_id}-{trace_id}", tenant_id, "PromotionPublished", "v1", trace_id, {"promotion_id": promotion.promotion_id, "product_id": promotion.product_id}, datetime.now(UTC))
        self._outbox.commit_business_event(event, lambda cursor: self._insert_promotion(cursor, tenant_id, promotion))

    def commit_coupon(self, tenant_id: str, coupon_code: str, quote_key: str, trace_id: str) -> None:
        event = DurableEvent(f"CouponCommitted-{tenant_id}-{coupon_code}-{trace_id}", tenant_id, "CouponCommitted", "v1", trace_id, {"coupon_code": coupon_code, "quote_key": quote_key}, datetime.now(UTC))
        self._outbox.commit_business_event(event, lambda cursor: self._insert_coupon(cursor, tenant_id, coupon_code, quote_key))

    @staticmethod
    def _insert_coupon(cursor: psycopg.Cursor[Any], tenant_id: str, coupon_code: str, quote_key: str) -> bool:
        cursor.execute("INSERT INTO coupon_commits (tenant_id, coupon_code, quote_key) VALUES (%s, %s, %s) ON CONFLICT (tenant_id, coupon_code) DO NOTHING RETURNING quote_key", (tenant_id, coupon_code, quote_key))
        if cursor.fetchone() is not None:
            return True
        cursor.execute("SELECT quote_key FROM coupon_commits WHERE tenant_id = %s AND coupon_code = %s", (tenant_id, coupon_code))
        existing = cursor.fetchone()
        if existing is None:
            raise LookupError("coupon commitment was not persisted")
        if existing[0] != quote_key:
            raise CouponAlreadyCommittedError("coupon was already committed")
        return False

    @staticmethod
    def _insert_price(cursor: psycopg.Cursor[Any], tenant_id: str, entry: PriceEntry) -> None:
        scope_key = "|".join(value or "*" for value in entry.scope.values)
        cursor.execute("INSERT INTO price_entries (price_entry_id, tenant_id, price_list_id, product_id, store_id, channel_id, segment_id, currency, amount, effective_from, effective_until) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (f"{tenant_id}|{entry.price_list_id}|{entry.product_id}|{scope_key}|{entry.effective_from.isoformat()}", tenant_id, entry.price_list_id, entry.product_id, entry.scope.store_id, entry.scope.channel_id, entry.scope.segment_id, entry.scope.currency, entry.amount, entry.effective_from, entry.effective_until))

    @staticmethod
    def _insert_promotion(cursor: psycopg.Cursor[Any], tenant_id: str, promotion: Promotion) -> None:
        cursor.execute("INSERT INTO promotions (tenant_id, promotion_id, product_id, store_id, channel_id, segment_id, currency, discount_percent, priority, stackable, coupon_code, effective_from, effective_until) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (tenant_id, promotion.promotion_id, promotion.product_id, promotion.scope.store_id, promotion.scope.channel_id, promotion.scope.segment_id, promotion.scope.currency, promotion.discount_percent, promotion.priority, promotion.stackable, promotion.coupon_code, promotion.effective_from, promotion.effective_until))
