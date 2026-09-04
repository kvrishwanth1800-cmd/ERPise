from foundation.health import HealthCheckResult, is_healthy


def test_healthy_dependency_is_healthy() -> None:
    assert is_healthy(HealthCheckResult(service='postgres', status='healthy'))
