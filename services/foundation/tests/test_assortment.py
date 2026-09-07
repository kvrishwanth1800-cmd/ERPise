from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from foundation.access import (
    AuthorizationDeniedError,
    AuthorizationService,
    PermissionGrant,
    SessionRevocationService,
)
from foundation.assortment import (
    AssortmentCommand,
    AssortmentPublicationService,
    AssortmentScope,
)
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def service() -> AssortmentPublicationService:
    authorization = AuthorizationService(SessionRevocationService())
    authorization.grant(
        PermissionGrant("merchandiser", "tenant-a", "assortment.write")
    )
    return AssortmentPublicationService(authorization, AuditRecorder())


def tenant(tenant_id: str = "tenant-a") -> ScopeContext:
    return ScopeContext(tenant_id=tenant_id, is_tenant_administrator=True)


def command(
    assortment_id: str,
    scope: AssortmentScope,
    products: tuple[str, ...],
    start: datetime = NOW - timedelta(days=1),
    end: datetime | None = None,
) -> AssortmentCommand:
    return AssortmentCommand(assortment_id, scope, products, start, end)


def publish(
    subject: AssortmentPublicationService, assortment: AssortmentCommand
) -> None:
    subject.publish("merchandiser", "session", tenant(), assortment, "trace")


def test_effective_scope_makes_only_eligible_products_available() -> None:
    subject = service()
    publish(
        subject,
        command("channel", AssortmentScope(channel_id="web"), ("tea",)),
    )

    result = subject.published_eligibility(
        tenant(), AssortmentScope(channel_id="web"), NOW
    )

    assert result.assortment_id == "channel"
    assert result.product_ids == ("tea",)


def test_not_yet_effective_and_expired_assortments_are_excluded() -> None:
    subject = service()
    publish(
        subject,
        command(
            "future",
            AssortmentScope(channel_id="web"),
            ("tea",),
            NOW + timedelta(days=1),
        ),
    )
    publish(
        subject,
        command(
            "expired",
            AssortmentScope(channel_id="web"),
            ("coffee",),
            NOW - timedelta(days=2),
            NOW - timedelta(days=1),
        ),
    )

    result = subject.published_eligibility(
        tenant(), AssortmentScope(channel_id="web"), NOW
    )

    assert result.product_ids == ()


def test_preview_uses_same_eligibility_result_as_publication() -> None:
    subject = service()
    publish(
        subject,
        command(
            "segment",
            AssortmentScope(channel_id="web", segment_id="vip"),
            ("tea",),
        ),
    )
    scope = AssortmentScope(channel_id="web", segment_id="vip")

    assert subject.preview(tenant(), scope, NOW) == subject.published_eligibility(
        tenant(), scope, NOW
    )


def test_most_specific_scope_wins_then_latest_start_and_id_break_ties() -> None:
    subject = service()
    publish(
        subject,
        command("channel", AssortmentScope(channel_id="web"), ("tea",)),
    )
    publish(
        subject,
        command(
            "store",
            AssortmentScope(store_id="shop-1", channel_id="web"),
            ("coffee",),
        ),
    )
    publish(
        subject,
        command(
            "newer",
            AssortmentScope(store_id="shop-2"),
            ("juice",),
            NOW - timedelta(hours=1),
        ),
    )
    publish(
        subject,
        command(
            "older",
            AssortmentScope(store_id="shop-2"),
            ("water",),
            NOW - timedelta(days=1),
        ),
    )

    store_result = subject.preview(
        tenant(), AssortmentScope(store_id="shop-1", channel_id="web"), NOW
    )
    tie_result = subject.preview(
        tenant(), AssortmentScope(store_id="shop-2"), NOW
    )

    assert store_result.product_ids == ("coffee",)
    assert tie_result.product_ids == ("juice",)


def test_tenant_isolation_excludes_other_tenant_assortments() -> None:
    subject = service()
    publish(
        subject,
        command("channel", AssortmentScope(channel_id="web"), ("tea",)),
    )

    result = subject.preview(
        tenant("tenant-b"), AssortmentScope(channel_id="web"), NOW
    )

    assert result.product_ids == ()


def test_unauthorized_caller_cannot_publish_assortments() -> None:
    subject = service()

    with pytest.raises(AuthorizationDeniedError):
        subject.publish(
            "other",
            "session",
            tenant(),
            command("web", AssortmentScope(channel_id="web"), ("tea",)),
            "trace",
        )
