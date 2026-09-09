from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from foundation.operations import OperationsEvidenceError, OperationsEvidenceService


@pytest.fixture
def operations_service() -> Generator[OperationsEvidenceService, None, None]:
    database_url = "postgresql://erpise:change-me-local-only@localhost:5432/erpise"
    service = OperationsEvidenceService(database_url)
    migration = Path("services/foundation/migrations/0001_operations_evidence.up.sql")
    service.apply_migration(migration)
    yield service
    service.apply_migration(Path("services/foundation/migrations/0001_operations_evidence.down.sql"))
    service.close()


@pytest.mark.integration
def test_trace_evidence_correlates_a_tenant_request_to_v1_event(
    operations_service: OperationsEvidenceService,
) -> None:
    evidence = operations_service.record_trace_evidence(
        f"evidence-{uuid4()}",
        "tenant-a",
        "trace-a",
        "event-a",
        "order.committed",
        "v1",
    )

    assert operations_service.trace_evidence("tenant-a", "trace-a") == (evidence,)
    assert operations_service.trace_evidence("tenant-b", "trace-a") == ()


@pytest.mark.integration
def test_breached_objective_creates_an_actionable_alert(
    operations_service: OperationsEvidenceService,
) -> None:
    alert = operations_service.evaluate_objective(
        f"alert-{uuid4()}",
        "tenant-a",
        "event-lag-seconds",
        61.0,
        60.0,
        "trace-a",
        "replay pending events",
    )

    assert alert is not None
    assert alert.action == "replay pending events"
    assert (
        operations_service.evaluate_objective(
            f"alert-{uuid4()}",
            "tenant-a",
            "event-lag-seconds",
            60.0,
            60.0,
            "trace-a",
            "ignore",
        )
        is None
    )


@pytest.mark.integration
def test_restore_exercise_records_failed_and_successful_outcomes(
    operations_service: OperationsEvidenceService,
) -> None:
    failed = operations_service.record_restore_exercise(
        f"restore-{uuid4()}", "tenant-a", "trace-a", True, False
    )
    restored = operations_service.record_restore_exercise(
        f"restore-{uuid4()}", "tenant-a", "trace-a", True, True
    )

    assert failed.service_behavior_restored is False
    assert restored.data_restored is True
    assert restored.service_behavior_restored is True


@pytest.mark.integration
def test_telemetry_redacts_secrets_and_card_data(
    operations_service: OperationsEvidenceService,
) -> None:
    operations_service.record_telemetry(
        f"telemetry-{uuid4()}",
        "tenant-a",
        "trace-a",
        {
            "api_token": "secret-value",
            "message": "card 4111 1111 1111 1111",
            "safe": "value",
        },
    )

    assert operations_service.telemetry_details("tenant-a", "trace-a") == {
        "api_token": "[REDACTED]",
        "message": "card [REDACTED]",
        "safe": "value",
    }


@pytest.mark.integration
def test_retention_requires_scoped_timezone_aware_cutoff(
    operations_service: OperationsEvidenceService,
) -> None:
    with pytest.raises(OperationsEvidenceError, match="timezone-aware"):
        operations_service.prune_before("tenant-a", datetime.now())

    assert operations_service.prune_before("tenant-a", datetime.now(UTC) + timedelta(days=1)) == 0
