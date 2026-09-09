"""Governed, deterministic analytics projections and report execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext

MONEY_SCALE = Decimal("0.01")


@dataclass(frozen=True)
class OperationalMetricEvent:
    event_id: str
    tenant_id: str
    occurred_at: datetime
    metric: str
    amount: Decimal
    currency: str
    store_id: str | None = None
    warehouse_id: str | None = None
    channel: str | None = None
    entity_id: str | None = None


@dataclass(frozen=True)
class ReportFilter:
    starts_on: date
    ends_on: date
    store_id: str | None = None
    warehouse_id: str | None = None
    channel: str | None = None
    entity_id: str | None = None


@dataclass(frozen=True)
class MetricProjection:
    metric: str
    currency: str
    value: Decimal
    event_count: int
    freshness: datetime | None
    definition: str
    reconciled: bool
    reconciliation_exception: str | None


@dataclass(frozen=True)
class ReportResult:
    tenant_id: str
    filters: ReportFilter
    projections: tuple[MetricProjection, ...]
    generated_at: datetime


class AnalyticsProjector:
    """Maintains replay-safe metric facts. It never queries operational tables."""

    def __init__(self) -> None:
        self._events: dict[str, OperationalMetricEvent] = {}

    def apply(self, event: OperationalMetricEvent) -> bool:
        if event.occurred_at.tzinfo is None:
            raise ValueError("metric event timestamps must be timezone-aware")
        if not event.event_id or not event.tenant_id or not event.metric:
            raise ValueError("metric events require identifiers and a metric")
        if event.amount.is_nan() or event.amount.is_infinite():
            raise ValueError("metric amounts must be finite")
        if event.event_id in self._events:
            return False
        self._events[event.event_id] = event
        return True

    def rebuild(self, tenant_id: str, events: Iterable[OperationalMetricEvent]) -> int:
        self._events = {
            event_id: event
            for event_id, event in self._events.items()
            if event.tenant_id != tenant_id
        }
        applied = 0
        for event in sorted(events, key=lambda item: (item.occurred_at, item.event_id)):
            if event.tenant_id == tenant_id and self.apply(event):
                applied += 1
        return applied

    def report(self, tenant_id: str, filters: ReportFilter) -> tuple[MetricProjection, ...]:
        if filters.ends_on < filters.starts_on:
            raise ValueError("report end date must not precede start date")
        grouped: dict[tuple[str, str], list[OperationalMetricEvent]] = {}
        for event in self._events.values():
            if event.tenant_id != tenant_id or not self._matches(event, filters):
                continue
            grouped.setdefault((event.metric, event.currency), []).append(event)
        result: list[MetricProjection] = []
        for (metric, currency), events in sorted(grouped.items()):
            value = sum((event.amount for event in events), Decimal()).quantize(
                MONEY_SCALE, rounding=ROUND_HALF_UP
            )
            result.append(
                MetricProjection(
                    metric=metric,
                    currency=currency,
                    value=value,
                    event_count=len(events),
                    freshness=max(event.occurred_at for event in events),
                    definition=f"sum({metric}) from committed operational events",
                    reconciled=True,
                    reconciliation_exception=None,
                )
            )
        return tuple(result)

    @staticmethod
    def _matches(event: OperationalMetricEvent, filters: ReportFilter) -> bool:
        event_date = event.occurred_at.astimezone(UTC).date()
        return (
            filters.starts_on <= event_date <= filters.ends_on
            and (filters.store_id is None or event.store_id == filters.store_id)
            and (filters.warehouse_id is None or event.warehouse_id == filters.warehouse_id)
            and (filters.channel is None or event.channel == filters.channel)
            and (filters.entity_id is None or event.entity_id == filters.entity_id)
        )


class ReportingService:
    """Applies authorization, scope, and audit policy around governed projections."""

    def __init__(
        self,
        projector: AnalyticsProjector,
        authorization: AuthorizationService,
        audit: AuditRecorder,
    ) -> None:
        self._projector = projector
        self._authorization = authorization
        self._audit = audit

    def generate(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        filters: ReportFilter,
        trace_id: str,
    ) -> ReportResult:
        self._authorization.authorize(
            principal_id, session_id, scope, "analytics.report.read", filters.entity_id
        )
        report = ReportResult(
            tenant_id=scope.tenant_id,
            filters=filters,
            projections=self._projector.report(scope.tenant_id, filters),
            generated_at=datetime.now(UTC),
        )
        self._audit.record(
            principal_id, "authorized", "analytics.report", "report generated",
            "tenant-scoped-reporting", trace_id, "completed"
        )
        return report

    def export_csv(
        self,
        principal_id: str,
        session_id: str,
        scope: ScopeContext,
        filters: ReportFilter,
        trace_id: str,
    ) -> str:
        self._authorization.authorize(
            principal_id, session_id, scope, "analytics.report.export", filters.entity_id
        )
        report = self._projector.report(scope.tenant_id, filters)
        rows = ["metric,currency,value,event_count,freshness,definition,reconciled"]
        for projection in report:
            rows.append(
                ",".join((
                    projection.metric, projection.currency, str(projection.value),
                    str(projection.event_count),
                    projection.freshness.isoformat() if projection.freshness else "",
                    projection.definition, str(projection.reconciled).lower(),
                ))
            )
        self._audit.record(
            principal_id, "authorized", "analytics.export", "scoped export completed",
            "tenant-scoped-reporting", trace_id, "completed"
        )
        return "\n".join(rows) + "\n"
