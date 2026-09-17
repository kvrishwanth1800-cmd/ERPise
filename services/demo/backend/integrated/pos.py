"""Tenant-scoped KVR cash POS transaction and register adapter."""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from integrated.persistence import connect

BUSINESS_NAME = "KVR"
CURRENCY_CODE = "SGD"


def quote(tenant_id: str, lines: Sequence[dict[str, Any]]) -> dict[str, object]:
    """Return a deterministic cash-sale quote for eligible catalog lines."""
    normalized = _normalize_lines(lines)
    quoted: list[dict[str, object]] = []
    total = 0
    with connect() as connection, connection.cursor() as cursor:
        for line in normalized:
            cursor.execute(
                "SELECT name, price_cents, available FROM demo_products WHERE tenant_id = %s AND product_id = %s",
                (tenant_id, line["product_id"]),
            )
            row = cursor.fetchone()
            if row is None or int(cast(int, row[2])) < int(line["quantity"]):
                raise ValueError("inventory_unavailable")
            line_total = int(cast(int, row[1])) * int(line["quantity"])
            total += line_total
            quoted.append({"product_id": line["product_id"], "name": str(row[0]), "quantity": line["quantity"], "unit_price_cents": int(row[1]), "line_total_cents": line_total})
    return {"currency": CURRENCY_CODE, "lines": quoted, "subtotal_cents": total, "total_cents": total, "tax_cents": 0}


def open_shift(tenant_id: str, cashier_id: str, opening_float_cents: int) -> dict[str, object]:
    if opening_float_cents < 0:
        raise ValueError("invalid_opening_float")
    shift_id = f"shift-{uuid.uuid4()}"
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM kvr_shifts WHERE tenant_id = %s AND cashier_id = %s AND closed_at IS NULL", (tenant_id, cashier_id))
        if cursor.fetchone() is not None:
            raise ValueError("shift_already_open")
        cursor.execute("INSERT INTO kvr_shifts (tenant_id, shift_id, cashier_id, opening_float_cents) VALUES (%s, %s, %s, %s)", (tenant_id, shift_id, cashier_id, opening_float_cents))
    return {"shift_id": shift_id, "status": "open", "opening_float_cents": opening_float_cents, "currency": CURRENCY_CODE}


def close_shift(tenant_id: str, shift_id: str, declared_cash_cents: int) -> dict[str, object]:
    if declared_cash_cents < 0:
        raise ValueError("invalid_declared_cash")
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT opening_float_cents, closed_at FROM kvr_shifts WHERE tenant_id = %s AND shift_id = %s FOR UPDATE", (tenant_id, shift_id))
        shift = cursor.fetchone()
        if shift is None:
            raise ValueError("shift_not_found")
        if shift[1] is not None:
            raise ValueError("shift_already_closed")
        cursor.execute("SELECT COALESCE(SUM(total_cents - change_cents), 0) FROM kvr_sales WHERE tenant_id = %s AND shift_id = %s AND payment_method = 'cash' AND status = 'completed'", (tenant_id, shift_id))
        cash_sales = int(cast(tuple[int], cursor.fetchone())[0])
        recorded = int(shift[0]) + cash_sales
        cursor.execute("UPDATE kvr_shifts SET closed_at = now(), declared_cash_cents = %s, recorded_cash_cents = %s WHERE tenant_id = %s AND shift_id = %s", (declared_cash_cents, recorded, tenant_id, shift_id))
    return {"shift_id": shift_id, "status": "closed", "declared_cash_cents": declared_cash_cents, "recorded_cash_cents": recorded, "variance_cents": declared_cash_cents - recorded, "currency": CURRENCY_CODE}


def complete_cash_sale(tenant_id: str, cashier_id: str, shift_id: str, lines: Sequence[dict[str, Any]], tendered_cents: int, idempotency_key: str) -> dict[str, object]:
    if not idempotency_key or tendered_cents < 0:
        raise ValueError("invalid_sale")
    quoted = quote(tenant_id, lines)
    total = int(quoted["total_cents"])
    if tendered_cents < total:
        raise ValueError("insufficient_cash_tendered")
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{tenant_id}:{idempotency_key}",))
        cursor.execute("SELECT sale_id, total_cents, tendered_cents, change_cents FROM kvr_sales WHERE tenant_id = %s AND idempotency_key = %s", (tenant_id, idempotency_key))
        existing = cursor.fetchone()
        if existing is not None:
            return _sale_result(str(existing[0]), int(existing[1]), int(existing[2]), int(existing[3]), True)
        cursor.execute("SELECT closed_at FROM kvr_shifts WHERE tenant_id = %s AND shift_id = %s", (tenant_id, shift_id))
        shift = cursor.fetchone()
        if shift is None or shift[0] is not None:
            raise ValueError("open_shift_required")
        for line in cast(list[dict[str, object]], quoted["lines"]):
            cursor.execute("UPDATE demo_products SET available = available - %s WHERE tenant_id = %s AND product_id = %s AND available >= %s RETURNING name", (line["quantity"], tenant_id, line["product_id"], line["quantity"]))
            if cursor.fetchone() is None:
                raise ValueError("inventory_unavailable")
        sale_id, receipt_number = f"sale-{uuid.uuid4()}", f"KVR-{uuid.uuid4().hex[:10].upper()}"
        change = tendered_cents - total
        cursor.execute("INSERT INTO kvr_sales (tenant_id, sale_id, shift_id, cashier_id, subtotal_cents, total_cents, payment_method, tendered_cents, change_cents, status, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,'cash',%s,%s,'completed',%s)", (tenant_id, sale_id, shift_id, cashier_id, total, total, tendered_cents, change, idempotency_key))
        for number, line in enumerate(cast(list[dict[str, object]], quoted["lines"]), start=1):
            cursor.execute("INSERT INTO kvr_sale_lines (tenant_id,sale_id,line_number,product_id,product_name,quantity,unit_price_cents,line_total_cents) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (tenant_id, sale_id, number, line["product_id"], line["name"], line["quantity"], line["unit_price_cents"], line["line_total_cents"]))
            cursor.execute("INSERT INTO kvr_inventory_movements (tenant_id,movement_id,product_id,quantity_delta,reason,reference_id) VALUES (%s,%s,%s,%s,'sale',%s)", (tenant_id, f"movement-{uuid.uuid4()}", line["product_id"], -int(cast(int, line["quantity"])), sale_id))
        cursor.execute("INSERT INTO kvr_receipts (tenant_id,receipt_number,sale_id,business_name,currency_code,total_cents) VALUES (%s,%s,%s,%s,%s,%s)", (tenant_id, receipt_number, sale_id, BUSINESS_NAME, CURRENCY_CODE, total))
    return {**_sale_result(sale_id, total, tendered_cents, change, False), "receipt_number": receipt_number, "business_name": BUSINESS_NAME, "lines": quoted["lines"]}


def return_sale(tenant_id: str, sale_id: str) -> dict[str, object]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM kvr_sales WHERE tenant_id = %s AND sale_id = %s FOR UPDATE", (tenant_id, sale_id))
        sale = cursor.fetchone()
        if sale is None:
            raise ValueError("sale_not_found")
        if str(sale[0]) != "completed":
            raise ValueError("sale_not_returnable")
        cursor.execute("SELECT product_id, quantity FROM kvr_sale_lines WHERE tenant_id = %s AND sale_id = %s ORDER BY line_number", (tenant_id, sale_id))
        lines = cursor.fetchall()
        for product_id, quantity in lines:
            cursor.execute("UPDATE demo_products SET available = available + %s WHERE tenant_id = %s AND product_id = %s", (quantity, tenant_id, product_id))
            cursor.execute("INSERT INTO kvr_inventory_movements (tenant_id,movement_id,product_id,quantity_delta,reason,reference_id) VALUES (%s,%s,%s,%s,'return',%s)", (tenant_id, f"movement-{uuid.uuid4()}", product_id, quantity, sale_id))
        cursor.execute("UPDATE kvr_sales SET status = 'returned' WHERE tenant_id = %s AND sale_id = %s", (tenant_id, sale_id))
    return {"sale_id": sale_id, "status": "returned", "stock_restored": True}


def _normalize_lines(lines: Sequence[dict[str, Any]]) -> list[dict[str, object]]:
    if not lines:
        raise ValueError("empty_cart")
    combined: dict[str, int] = {}
    for line in lines:
        product_id, quantity = str(line.get("product_id", "")), int(line.get("quantity", 0))
        if not product_id or quantity < 1:
            raise ValueError("invalid_cart_line")
        combined[product_id] = combined.get(product_id, 0) + quantity
    return [{"product_id": product_id, "quantity": quantity} for product_id, quantity in sorted(combined.items())]


def _sale_result(sale_id: str, total: int, tendered: int, change: int, idempotent: bool) -> dict[str, object]:
    return {"sale_id": sale_id, "status": "completed", "payment_method": "cash", "currency": CURRENCY_CODE, "total_cents": total, "tendered_cents": tendered, "change_cents": change, "idempotent": idempotent}
