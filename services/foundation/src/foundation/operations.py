"""Persistent, tenant-scoped operations evidence with safe telemetry output."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


class OperationsEvidenceError(ValueError):
    """Raised when operations evidence cannot satisfy its safety contract."""


@dataclass(frozen=True)
class TraceEvidence:
    evidence_id: str
    tenant_id: str
    trace_id: str
    event_id: str
    event_type: str
    event_version: str
    occurred_at: datetime


@dataclass(frozen=True)
class ActionableAlert:
    alert_id: str
    tenant_id: str
    objective_name: str
    observed_value: float
    threshold_value: float
    trace_id: str
    action: str
    occurred_at: datetime


@dataclass(frozen=True)
class RestoreExercise:
    evidence_id: str
    tenant_id: str
    trace_id: str
    data_restored: bool
    service_behavior_restored: bool
    occurred_at: datetime


class OperationsEvidenceService:
    """Records append-only operations evidence and deterministic release alerts."""

    _secret_key = re.compile(r"(password|secret|token|authorization|card|pan)", re.IGNORECASE)
    _card_number = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

    def __init__(self, database_url: str) -> None:
        self._connection = psycopg.connect(database_url, row_factory=dict_row)

    def close(self) -> None:
        self._connection.close()

    def apply_migration(self, migration_path: Path) -> None:
        self._connection.execute(migration_path.read_text(encoding="utf-8"))
        self._connection.commit()

    def record_trace_evidence(
        self,
        evidence_id: str,
        tenant_id: str,
        trace_id: str,
        event_id: str,
        event_type: str,
        event_version: str,
    ) -> TraceEvidence:
        self._require_values(evidence_id, tenant_id, trace_id, event_id, event_type, event_version)
        occurred_at = datetime.now(UTC)
        self._insert_evidence(
            evidence_id,
            tenant_id,
            "trace-event",
            trace_id,
            event_id,
            event_type,
            event_version,
            "recorded",
            {},
            occurred_at,
        )
        return TraceEvidence(
            evidence_id,
            tenant_id,
            trace_id,
            event_id,
            event_type,
            event_version,
            occurred_at,
        )

    def record_telemetry(
        self,
        evidence_id: str,
        tenant_id: str,
        trace_id: str,
        telemetry: Mapping[str, object],
    ) -> None:
        self._require_values(evidence_id, tenant_id, trace_id)
        self._insert_evidence(
            evidence_id,
            tenant_id,
            "telemetry",
            trace_id,
            None,
            None,
            None,
            "recorded",
            self._redact(telemetry),
            datetime.now(UTC),
        )

    def evaluate_objective(
        self,
        alert_id: str,
        tenant_id: str,
        objective_name: str,
        observed_value: float,
        threshold_value: float,
        trace_id: str,
        action: str,
    ) -> ActionableAlert | None:
        self._require_values(alert_id, tenant_id, objective_name, trace_id, action)
        if observed_value <= threshold_value:
            return None
        occurred_at = datetime.now(UTC)
        self._connection.execute(
            """
            INSERT INTO operations_alerts (
              alert_id, tenant_id, objective_name, observed_value, threshold_value,
              trace_id, status, action, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                alert_id,
                tenant_id,
                objective_name,
                observed_value,
                threshold_value,
                trace_id,
                "open",
                action,
                occurred_at,
            ),
        )
        self._connection.commit()
        return ActionableAlert(
            alert_id,
            tenant_id,
            objective_name,
            observed_value,
            threshold_value,
            trace_id,
            action,
            occurred_at,
        )

    def record_restore_exercise(
        self,
        evidence_id: str,
        tenant_id: str,
        trace_id: str,
        data_restored: bool,
        service_behavior_restored: bool,
    ) -> RestoreExercise:
        self._require_values(evidence_id, tenant_id, trace_id)
        occurred_at = datetime.now(UTC)
        outcome = "restored" if data_restored and service_behavior_restored else "not-restored"
        self._insert_evidence(
            evidence_id,
            tenant_id,
            "restore-exercise",
            trace_id,
            None,
            None,
            None,
            outcome,
            {
                "data_restored": data_restored,
                "service_behavior_restored": service_behavior_restored,
            },
            occurred_at,
        )
        return RestoreExercise(
            evidence_id,
            tenant_id,
            trace_id,
            data_restored,
            service_behavior_restored,
            occurred_at,
        )

    def trace_evidence(self, tenant_id: str, trace_id: str) -> tuple[TraceEvidence, ...]:
        self._require_values(tenant_id, trace_id)
        rows = self._connection.execute(
            """
            SELECT
              evidence_id, tenant_id, trace_id, event_id, event_type, event_version, occurred_at
            FROM operations_evidence
            WHERE tenant_id = %s AND trace_id = %s AND evidence_type = 'trace-event'
            ORDER BY occurred_at, evidence_id
            """,
            (tenant_id, trace_id),
        ).fetchall()
        return tuple(
            TraceEvidence(
                str(row["evidence_id"]),
                str(row["tenant_id"]),
                str(row["trace_id"]),
                str(row["event_id"]),
                str(row["event_type"]),
                str(row["event_version"]),
                row["occurred_at"],
            )
            for row in rows
        )

    def telemetry_details(self, tenant_id: str, trace_id: str) -> Mapping[str, object]:
        row = self._connection.execute(
            """
            SELECT details FROM operations_evidence
            WHERE tenant_id = %s AND trace_id = %s AND evidence_type = 'telemetry'
            ORDER BY occurred_at DESC LIMIT 1
            """,
            (tenant_id, trace_id),
        ).fetchone()
        if row is None:
            raise OperationsEvidenceError(
                "Telemetry evidence does not exist for this tenant trace."
            )
        return row["details"]

    def prune_before(self, tenant_id: str, cutoff: datetime) -> int:
        self._require_values(tenant_id)
        if cutoff.tzinfo is None:
            raise OperationsEvidenceError("Retention cutoff must be timezone-aware.")
        with self._connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM operations_evidence WHERE tenant_id = %s AND occurred_at < %s",
                (tenant_id, cutoff),
            )
            deleted = cursor.rowcount
        self._connection.commit()
        return deleted

    def _insert_evidence(
        self,
        evidence_id: str,
        tenant_id: str,
        evidence_type: str,
        trace_id: str,
        event_id: str | None,
        event_type: str | None,
        event_version: str | None,
        outcome: str,
        details: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO operations_evidence (
              evidence_id, tenant_id, evidence_type, trace_id, event_id, event_type,
              event_version, outcome, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                evidence_id,
                tenant_id,
                evidence_type,
                trace_id,
                event_id,
                event_type,
                event_version,
                outcome,
                json.dumps(details),
                occurred_at,
            ),
        )
        self._connection.commit()

    @classmethod
    def _redact(cls, telemetry: Mapping[str, object]) -> dict[str, object]:
        return {
            key: "[REDACTED]" if cls._secret_key.search(key) else cls._redact_value(value)
            for key, value in telemetry.items()
        }

    @classmethod
    def _redact_value(cls, value: object) -> object:
        if isinstance(value, str):
            return cls._card_number.sub("[REDACTED]", value)
        if isinstance(value, Mapping):
            return cls._redact(value)
        if isinstance(value, list):
            return [cls._redact_value(item) for item in value]
        return value

    @staticmethod
    def _require_values(*values: str) -> None:
        if any(not value for value in values):
            raise OperationsEvidenceError(
                "Evidence identifiers, tenant scope, and trace context are required."
            )
