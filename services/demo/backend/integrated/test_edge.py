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


def test_reserve_uses_internal_client_and_idempotency_key() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        assert edge.reserve("tenant-a", "reservation-a", 1, "key-a") == {"status": "reserved"}
    request = open_call.call_args.args[0]
    assert request.full_url.endswith("/reserve")
    assert request.data == b'{"reservation_id": "reservation-a", "quantity": 1}'


def test_transition_uses_requested_lifecycle_path() -> None:
    with patch("integrated.edge.urlopen", return_value=FakeResponse()) as open_call:
        edge.transition("tenant-a", "release", "reservation-a")
    assert open_call.call_args.args[0].full_url.endswith("/release")
