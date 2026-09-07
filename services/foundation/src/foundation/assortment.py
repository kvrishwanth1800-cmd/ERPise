"""Tenant-scoped, effective-dated assortment publication and preview."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from foundation.access import AuthorizationService
from foundation.audit import AuditRecorder
from foundation.organization import ScopeContext


class AssortmentValidationError(ValueError):
    """Raised when an assortment publication is invalid."""


@dataclass(frozen=True)
class AssortmentScope:
    store_id: str | None = None
    channel_id: str | None = None
    segment_id: str | None = None

    @property
    def specificity(self) -> int:
        return sum(value is not None for value in self.values)

    @property
    def values(self) -> tuple[str | None, str | None, str | None]:
        return self.store_id, self.channel_id, self.segment_id

    def matches(self, query: AssortmentScope) -> bool:
        return all(
            expected is None or expected == actual
            for expected, actual in zip(self.values, query.values, strict=True)
        )


@dataclass(frozen=True)
class AssortmentCommand:
    assortment_id: str
    scope: AssortmentScope
    product_ids: tuple[str, ...]
    effective_from: datetime
    effective_until: datetime | None = None


@dataclass(frozen=True)
class AssortmentRecord:
    assortment_id: str
    tenant_id: str
    scope: AssortmentScope
    product_ids: tuple[str, ...]
    effective_from: datetime
    effective_until: datetime | None

    def is_effective_at(self, at: datetime) -> bool:
        return self.effective_from <= at and (
            self.effective_until is None or at < self.effective_until
        )


@dataclass(frozen=True)
class AssortmentEligibility:
    assortment_id: str | None
    product_ids: tuple[str, ...]


@dataclass(frozen=True)
class AssortmentEvent:
    event_type: str
    assortment_id: str
    tenant_id: str
    trace_id: str


class AssortmentPublicationService:
    """Owns scoped assortment publication and a shared eligibility evaluator."""

    def __init__(self, authorization: AuthorizationService, audit: AuditRecorder) -> None:
        self._authorization = authorization
        self._audit = audit
        self._assortments: dict[tuple[str, str], AssortmentRecord] = {}
        self.outbox: list[AssortmentEvent] = []

    def publish(
        self,
        principal_id: str,
        session_id: str,
        tenant_scope: ScopeContext,
        command: AssortmentCommand,
        trace_id: str,
    ) -> AssortmentRecord:
        self._authorization.authorize(
            principal_id, session_id, tenant_scope, "assortment.write"
        )
        self._validate(command)
        record = AssortmentRecord(
            command.assortment_id,
            tenant_scope.tenant_id,
            command.scope,
            command.product_ids,
            command.effective_from,
            command.effective_until,
        )
        self._assortments[(tenant_scope.tenant_id, command.assortment_id)] = record
        self._audit.record(
            principal_id,
            "assortment-publication",
            "assortment",
            "published",
            "assortment.write",
            trace_id,
            command.assortment_id,
        )
        self.outbox.append(
            AssortmentEvent(
                "AssortmentPublished",
                command.assortment_id,
                tenant_scope.tenant_id,
                trace_id,
            )
        )
        return record

    def preview(
        self, tenant_scope: ScopeContext, scope: AssortmentScope, at: datetime
    ) -> AssortmentEligibility:
        return self._evaluate(tenant_scope.tenant_id, scope, at)

    def published_eligibility(
        self, tenant_scope: ScopeContext, scope: AssortmentScope, at: datetime
    ) -> AssortmentEligibility:
        return self._evaluate(tenant_scope.tenant_id, scope, at)

    def _evaluate(
        self, tenant_id: str, scope: AssortmentScope, at: datetime
    ) -> AssortmentEligibility:
        candidates = [
            record
            for (record_tenant_id, _), record in self._assortments.items()
            if record_tenant_id == tenant_id
            and record.scope.matches(scope)
            and record.is_effective_at(at)
        ]
        if not candidates:
            return AssortmentEligibility(None, ())
        selected = min(
            candidates,
            key=lambda record: (
                -record.scope.specificity,
                -record.effective_from.timestamp(),
                record.assortment_id,
            ),
        )
        return AssortmentEligibility(selected.assortment_id, selected.product_ids)

    @staticmethod
    def _validate(command: AssortmentCommand) -> None:
        if not command.assortment_id:
            raise AssortmentValidationError("assortment id is required")
        if command.scope.specificity == 0:
            raise AssortmentValidationError("at least one assortment scope is required")
        if not command.product_ids or any(not product_id for product_id in command.product_ids):
            raise AssortmentValidationError("at least one product id is required")
        if len(set(command.product_ids)) != len(command.product_ids):
            raise AssortmentValidationError("product ids must be unique")
        if command.effective_from.tzinfo is None:
            raise AssortmentValidationError("effective from must be timezone-aware")
        if command.effective_until is not None:
            if command.effective_until.tzinfo is None:
                raise AssortmentValidationError("effective until must be timezone-aware")
            if command.effective_until <= command.effective_from:
                raise AssortmentValidationError(
                    "effective until must be after effective from"
                )


class AssortmentAdministrationApi:
    """Typed command and query boundary for assortment publication."""

    def __init__(self, service: AssortmentPublicationService) -> None:
        self._service = service

    def publish_assortment(
        self,
        principal_id: str,
        session_id: str,
        tenant_scope: ScopeContext,
        command: AssortmentCommand,
        trace_id: str,
    ) -> AssortmentRecord:
        return self._service.publish(
            principal_id, session_id, tenant_scope, command, trace_id
        )

    def preview_assortment(
        self, tenant_scope: ScopeContext, scope: AssortmentScope, at: datetime
    ) -> AssortmentEligibility:
        return self._service.preview(tenant_scope, scope, at)
