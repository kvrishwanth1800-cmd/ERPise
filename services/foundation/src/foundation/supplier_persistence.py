# ruff: noqa: E501
"""PostgreSQL persistence for supplier governance facts and their integration events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import psycopg

from foundation.durable_outbox import DurableEvent, DurableOutboxStore
from foundation.supplier import (
    BankChangeRequest,
    BankDetails,
    Certification,
    ContractVersion,
    SupplierRecord,
)


class DurableSupplierStore:
    """Commits one supplier fact and its integration event per transaction."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection
        self._outbox = DurableOutboxStore(connection)

    def register_supplier(self, supplier: SupplierRecord, trace_id: str) -> None:
        self._commit(
            supplier.tenant_id,
            "SupplierChanged",
            supplier.supplier_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO suppliers (
                    tenant_id, supplier_id, name, status, responsible_organization_id
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    supplier.tenant_id,
                    supplier.supplier_id,
                    supplier.name,
                    supplier.status,
                    supplier.responsible_organization_id,
                ),
            ),
        )

    def change_status(
        self, tenant_id: str, supplier_id: str, status: str, trace_id: str
    ) -> None:
        def write(cursor: psycopg.Cursor[Any]) -> bool | None:
            cursor.execute(
                "UPDATE suppliers SET status = %s, version = version + 1, updated_at = now() "
                "WHERE tenant_id = %s AND supplier_id = %s AND status <> %s",
                (status, tenant_id, supplier_id, status),
            )
            if cursor.rowcount == 0:
                return False
            return None

        self._commit(tenant_id, "SupplierChanged", supplier_id, trace_id, write)

    def request_bank_change(self, request: BankChangeRequest) -> None:
        """Store a requested bank change. It stays inactive until dual control approves it."""
        with self._connection.transaction(), self._connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO supplier_bank_change_requests (
                    tenant_id, request_id, supplier_id, requester_id, account_holder,
                    account_number, bank_identifier, idempotency_key, status, requested_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                """,
                (
                    request.tenant_id,
                    request.request_id,
                    request.supplier_id,
                    request.requester_id,
                    request.proposed.account_holder,
                    request.proposed.account_number,
                    request.proposed.bank_identifier,
                    request.idempotency_key,
                    request.requested_at,
                ),
            )

    def approve_bank_change(
        self,
        tenant_id: str,
        request_id: str,
        approver_id: str,
        decided_at: datetime,
        trace_id: str,
    ) -> None:
        """Activate proposed bank data only when an eligible separate approver resolves it."""

        def write(cursor: psycopg.Cursor[Any]) -> bool | None:
            cursor.execute(
                "UPDATE supplier_bank_change_requests SET status = 'approved', approver_id = %s, "
                "decided_at = %s WHERE tenant_id = %s AND request_id = %s AND status = 'pending' "
                "AND requester_id <> %s RETURNING supplier_id",
                (approver_id, decided_at, tenant_id, request_id, approver_id),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                "UPDATE suppliers SET active_bank_change_id = %s, version = version + 1, "
                "updated_at = now() WHERE tenant_id = %s AND supplier_id = %s",
                (request_id, tenant_id, str(row[0])),
            )
            return None

        self._commit(tenant_id, "SupplierChanged", request_id, trace_id, write)

    def active_bank_details(self, tenant_id: str, supplier_id: str) -> BankDetails | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT request.account_holder, request.account_number, request.bank_identifier "
                "FROM suppliers supplier JOIN supplier_bank_change_requests request "
                "ON request.tenant_id = supplier.tenant_id "
                "AND request.request_id = supplier.active_bank_change_id "
                "WHERE supplier.tenant_id = %s AND supplier.supplier_id = %s "
                "AND request.status = 'approved'",
                (tenant_id, supplier_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return BankDetails(str(row[0]), str(row[1]), str(row[2]))

    def register_contract_version(self, version: ContractVersion, trace_id: str) -> None:
        self._commit(
            version.tenant_id,
            "SupplierChanged",
            version.version_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO supplier_contract_versions (
                    tenant_id, version_id, contract_id, supplier_id, version_number, terms,
                    effective_from, expires_on, status
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, 'registered')
                """,
                (
                    version.tenant_id,
                    version.version_id,
                    version.contract_id,
                    version.supplier_id,
                    version.version_number,
                    json.dumps(dict(version.terms)),
                    version.effective_from,
                    version.expires_on,
                ),
            ),
        )

    def activate_contract_version(
        self, tenant_id: str, version_id: str, trace_id: str
    ) -> None:
        """Activate one version and supersede the prior active version. Terms never change."""

        def write(cursor: psycopg.Cursor[Any]) -> bool | None:
            cursor.execute(
                "SELECT contract_id, status FROM supplier_contract_versions "
                "WHERE tenant_id = %s AND version_id = %s",
                (tenant_id, version_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise PermissionError("contract version is outside tenant scope")
            if str(row[1]) == "active":
                return False
            cursor.execute(
                "UPDATE supplier_contract_versions SET status = 'superseded' "
                "WHERE tenant_id = %s AND contract_id = %s AND status = 'active'",
                (tenant_id, str(row[0])),
            )
            cursor.execute(
                "UPDATE supplier_contract_versions SET status = 'active' "
                "WHERE tenant_id = %s AND version_id = %s",
                (tenant_id, version_id),
            )
            return None

        self._commit(tenant_id, "ContractActivated", version_id, trace_id, write)

    def effective_terms(
        self, tenant_id: str, contract_id: str, at: datetime
    ) -> Mapping[str, str] | None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT terms FROM supplier_contract_versions WHERE tenant_id = %s "
                "AND contract_id = %s AND status IN ('active', 'superseded') "
                "AND effective_from <= %s ORDER BY effective_from DESC, version_number DESC LIMIT 1",
                (tenant_id, contract_id, at),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return dict(row[0])

    def register_certification(self, certification: Certification, trace_id: str) -> None:
        self._commit(
            certification.tenant_id,
            "SupplierChanged",
            certification.certification_id,
            trace_id,
            lambda cursor: cursor.execute(
                """
                INSERT INTO supplier_certifications (
                    tenant_id, certification_id, supplier_id, kind, expires_on
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    certification.tenant_id,
                    certification.certification_id,
                    certification.supplier_id,
                    certification.kind,
                    certification.expires_on,
                ),
            ),
        )

    def advise_expiries(
        self, tenant_id: str, as_of: date, horizon: date, trace_id: str
    ) -> tuple[tuple[str, str, str, date], ...]:
        """Publish one advisory per approaching certification or active contract expiry."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 'certification', certification.certification_id, supplier.responsible_organization_id,
                       certification.expires_on, certification.supplier_id
                FROM supplier_certifications certification
                JOIN suppliers supplier
                  ON supplier.tenant_id = certification.tenant_id
                 AND supplier.supplier_id = certification.supplier_id
                WHERE certification.tenant_id = %s AND certification.expires_on BETWEEN %s AND %s
                UNION ALL
                SELECT 'contract', version.version_id, supplier.responsible_organization_id,
                       version.expires_on, version.supplier_id
                FROM supplier_contract_versions version
                JOIN suppliers supplier
                  ON supplier.tenant_id = version.tenant_id
                 AND supplier.supplier_id = version.supplier_id
                WHERE version.tenant_id = %s AND version.status = 'active'
                  AND version.expires_on BETWEEN %s AND %s
                ORDER BY 4, 2
                """,
                (tenant_id, as_of, horizon, tenant_id, as_of, horizon),
            )
            rows = cursor.fetchall()
        advisories: list[tuple[str, str, str, date]] = []
        for row in rows:
            subject_type, subject_id = str(row[0]), str(row[1])
            self._commit(
                tenant_id,
                "SupplierExpiryApproaching",
                f"{subject_type}-{subject_id}",
                trace_id,
                lambda cursor: None,
            )
            advisories.append((subject_type, subject_id, str(row[2]), row[3]))
        return tuple(advisories)

    def _commit(
        self,
        tenant_id: str,
        event_type: str,
        subject_id: str,
        trace_id: str,
        write: Any,
    ) -> None:
        event = DurableEvent(
            f"{event_type}-{tenant_id}-{subject_id}",
            tenant_id,
            event_type,
            "v1",
            trace_id,
            {"subject_id": subject_id},
            datetime.now(UTC),
        )
        self._outbox.commit_business_event(event, write)
