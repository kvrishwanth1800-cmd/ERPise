from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from foundation.access import AuthorizationDeniedError, AuthorizationService, PermissionGrant, SessionRevocationService
from foundation.analytics import AnalyticsProjector, OperationalMetricEvent, ReportFilter, ReportingService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


def event(event_id: str, tenant_id: str = "tenant-a", **values: object) -> OperationalMetricEvent:
    return OperationalMetricEvent(
        event_id, tenant_id, datetime(2026, 9, 9, 23, 30, tzinfo=UTC), "net_sales",
        Decimal("12.345"), "USD", store_id="store-a", warehouse_id="warehouse-a",
        channel="web", entity_id="entity-a", **values,
    )


def filters() -> ReportFilter:
    return ReportFilter(date(2026, 9, 9), date(2026, 9, 9), store_id="store-a")


def service() -> tuple[ReportingService, ScopeContext, AuditRecorder]:
    projector = AnalyticsProjector()
    projector.apply(event("one"))
    revocations = SessionRevocationService()
    authorization = AuthorizationService(revocations)
    authorization.grant(PermissionGrant("reader", "tenant-a", "analytics.report.read"))
    authorization.grant(PermissionGrant("reader", "tenant-a", "analytics.report.export"))
    scope = ScopeContext("tenant-a", frozenset({"entity-a"}))
    audit = AuditRecorder()
    return ReportingService(projector, authorization, audit), scope, audit


def test_projection_is_idempotent_exact_and_filterable() -> None:
    projector = AnalyticsProjector()
    assert projector.apply(event("one"))
    assert not projector.apply(event("one"))
    report = projector.report("tenant-a", filters())
    assert report[0].value == Decimal("12.35")
    assert report[0].event_count == 1
    assert report[0].freshness == datetime(2026, 9, 9, 23, 30, tzinfo=UTC)
    assert not projector.report("tenant-b", filters())


def test_report_requires_tenant_authorization_and_audits_export() -> None:
    reporting, scope, audit = service()
    assert reporting.generate("reader", "session", scope, filters(), "trace-report").projections
    csv = reporting.export_csv("reader", "session", scope, filters(), "trace-export")
    assert "12.35" in csv
    assert audit.records[-1].source == "analytics.export"
    with pytest.raises(AuthorizationDeniedError):
        reporting.generate("attacker", "session", scope, filters(), "trace-denied")


def test_rebuild_is_deterministic_and_dates_are_utc_bounded() -> None:
    projector = AnalyticsProjector()
    first = event("first")
    second = event("second", occurred_at=datetime(2026, 9, 10, 0, 0, tzinfo=UTC))
    assert projector.rebuild("tenant-a", (second, first, first)) == 2
    report = projector.report("tenant-a", ReportFilter(date(2026, 9, 9), date(2026, 9, 9)))
    assert report[0].event_count == 1
    assert report[0].value == Decimal("12.35")
