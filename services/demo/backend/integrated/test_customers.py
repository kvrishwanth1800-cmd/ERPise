from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrated import customers


def test_revoke_consent_updates_only_the_current_tenant() -> None:
    cursor = MagicMock()
    cursor.rowcount = 1
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("integrated.customers.connect", return_value=connection):
        result = customers.revoke_consent("tenant-a", "customer-a")

    assert result == {"customer_id": "customer-a", "consented": False}
    assert cursor.execute.call_args.args[1] == (False, "tenant-a", "customer-a")


def test_revoke_consent_rejects_unknown_customer() -> None:
    cursor = MagicMock()
    cursor.rowcount = 0
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch("integrated.customers.connect", return_value=connection):
        try:
            customers.revoke_consent("tenant-a", "missing")
        except ValueError as error:
            assert str(error) == "customer_not_found"
        else:
            raise AssertionError("unknown customer was accepted")
