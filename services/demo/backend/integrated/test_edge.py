from __future__ import annotations

from unittest.mock import patch

from integrated import edge


class FakeResponse:
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"reserved"}'


def test_reserve_sends_tenant_service_and_idempotency_headers() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        assert edge.reserve("tenant-a", "reservation-a", 1, "key-a") == {"status": "reserved"}
    request = open_call.call_args.args[0]
    assert request.get_header("X-tenant-id") == "tenant-a"
    assert request.get_header("X-edge-service-key") == edge.EDGE_SERVICE_KEY
    assert request.get_header("Idempotency-key") == "key-a"


def test_transition_sends_only_governed_tenant_context() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        edge.transition("tenant-a", "release", "reservation-a")
    request = open_call.call_args.args[0]
    assert request.full_url.endswith("/release")
    assert request.get_header("X-tenant-id") == "tenant-a"
