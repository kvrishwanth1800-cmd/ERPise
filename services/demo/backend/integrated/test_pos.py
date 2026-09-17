from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrated import pos


def test_quote_rejects_empty_cart() -> None:
    try:
        pos.quote("tenant-a", [])
    except ValueError as error:
        assert str(error) == "empty_cart"
    else:
        raise AssertionError("empty cart must be rejected")


def test_cash_sale_requires_enough_tender() -> None:
    with patch("integrated.pos.quote", return_value={"total_cents": 500, "lines": []}):
        try:
            pos.complete_cash_sale("tenant-a", "cashier-a", "shift-a", [{"product_id": "coffee", "quantity": 1}], 499, "sale-key")
        except ValueError as error:
            assert str(error) == "insufficient_cash_tendered"
        else:
            raise AssertionError("under-tendered cash sale must be rejected")


def test_open_shift_rejects_negative_float() -> None:
    try:
        pos.open_shift("tenant-a", "cashier-a", -1)
    except ValueError as error:
        assert str(error) == "invalid_opening_float"
    else:
        raise AssertionError("negative opening float must be rejected")


def test_quote_returns_priced_line() -> None:
    cursor = MagicMock()
    cursor.fetchone.return_value = ("Coffee", 499, 10)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value = cursor
    with patch("integrated.pos.connect", return_value=connection):
        result = pos.quote("tenant-a", [{"product_id": "coffee", "quantity": 2}])
    assert result["total_cents"] == 998
    assert result["currency"] == "SGD"
