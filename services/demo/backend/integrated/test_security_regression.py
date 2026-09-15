from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrated import orders


def test_checkout_locks_idempotency_key_before_stock_mutation() -> None:
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None, (499,)]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("integrated.orders.connect", return_value=connection):
        orders.create("tenant-a", "customer-a", "coffee", 1, "pickup", "same-key")

    assert cursor.execute.call_args_list[0].args[0].startswith("SELECT pg_advisory_xact_lock")
    assert cursor.execute.call_args_list[0].args[1] == ("tenant-a:same-key",)


def test_checkout_replay_returns_existing_order_without_stock_mutation() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = ("order-a", "payment-a", "reserved")
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("integrated.orders.connect", return_value=connection):
        result = orders.create("tenant-a", "customer-a", "coffee", 1, "pickup", "same-key")

    assert result["idempotent"] is True
    assert all("UPDATE demo_products" not in call.args[0] for call in cursor.execute.call_args_list)
