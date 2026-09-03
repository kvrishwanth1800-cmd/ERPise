from dataclasses import dataclass
from typing import Literal

HealthStatus = Literal['healthy', 'unhealthy']


@dataclass(frozen=True)
class HealthCheckResult:
    service: str
    status: HealthStatus


def is_healthy(result: HealthCheckResult) -> bool:
    return result.status == 'healthy'
