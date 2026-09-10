from pathlib import Path

SOURCE = Path("services/demo/backend/app.py").read_text()


def test_demo_adapter_keeps_login_and_session_routes() -> None:
    assert '"/api/auth/login"' in SOURCE
    assert '"/api/auth/logout"' in SOURCE
    assert "def require_session" in SOURCE
    assert "DEMO_MODE" in SOURCE


def test_checkout_uses_database_and_outbox_transaction() -> None:
    assert "INSERT INTO demo_orders" in SOURCE
    assert "INSERT INTO demo_outbox" in SOURCE
    assert "UPDATE demo_products SET available" in SOURCE


def test_projection_consumer_is_idempotent() -> None:
    assert "ON CONFLICT (event_id) DO NOTHING" in SOURCE
