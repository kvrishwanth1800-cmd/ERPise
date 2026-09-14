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


def normalized_headers(request: object) -> dict[str, str]:
    return {
        name.lower(): value
        for name, value in getattr(request, "header_items")()
    }


def test_reserve_sends_governed_headers() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        assert edge.reserve("tenant-a", "reservation-a", 1, "key-a") == {"status": "reserved"}
    headers = normalized_headers(open_call.call_args.args[0])
    assert headers["x-tenant-id"] == "tenant-a"
    assert headers["x-edge-service-key"] == edge.EDGE_SERVICE_KEY
    assert headers["idempotency-key"] == "key-a"


def test_transition_sends_governed_tenant_context() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        edge.transition("tenant-a", "release", "reservation-a")
    request = open_call.call_args.args[0]
    assert request.full_url.endswith("/release")
    assert normalized_headers(request)["x-tenant-id"] == "tenant-a"
