from services.demo.backend.integrated import edge


def test_reserve_and_transition_delegate_to_edge_request(monkeypatch):
    calls = []

    def capture(path, tenant_id, payload=None, idempotency_key=""):
        calls.append((path, tenant_id, payload, idempotency_key))
        return {"status": "ok"}

    monkeypatch.setattr(edge, "request", capture)

    assert edge.reserve("tenant-a", "reservation-a", 2, "key-a") == {"status": "ok"}
    assert edge.transition("tenant-a", "release", "reservation-a") == {"status": "ok"}
    assert calls == [
        ("/reserve", "tenant-a", {"reservation_id": "reservation-a", "quantity": 2}, "key-a"),
        ("/release", "tenant-a", {"reservation_id": "reservation-a"}, ""),
    ]
