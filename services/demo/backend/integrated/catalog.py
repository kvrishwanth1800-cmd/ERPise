"""Tenant-scoped KVR catalog, barcode lookup, and product import adapter."""
from __future__ import annotations

import uuid
from typing import Any, cast

from integrated.persistence import connect


def products(tenant_id: str) -> list[dict[str, object]]:
    """Read sellable catalog items and current available stock for one tenant."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT product_id, sku, name, price_cents, available, barcode, unit, lifecycle_status FROM demo_products WHERE tenant_id = %s ORDER BY sku", (tenant_id,))
        rows = cursor.fetchall()
    return [{"id": str(row[0]), "sku": str(row[1]), "name": str(row[2]), "price_cents": cast(int, row[3]), "available": cast(int, row[4]), "barcode": None if row[5] is None else str(row[5]), "unit": str(row[6]), "lifecycle_status": str(row[7])} for row in rows]


def search(tenant_id: str, query: str) -> list[dict[str, object]]:
    """Resolve product text, SKU, or barcode input for POS lookup."""
    value = query.strip()
    if not value:
        return []
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT product_id, sku, name, price_cents, available, barcode, unit, lifecycle_status FROM demo_products WHERE tenant_id = %s AND lifecycle_status = 'active' AND (barcode = %s OR sku ILIKE %s OR name ILIKE %s) ORDER BY sku LIMIT 30", (tenant_id, value, f"%{value}%", f"%{value}%"))
        rows = cursor.fetchall()
    return [{"id": str(row[0]), "sku": str(row[1]), "name": str(row[2]), "price_cents": cast(int, row[3]), "available": cast(int, row[4]), "barcode": None if row[5] is None else str(row[5]), "unit": str(row[6]), "lifecycle_status": str(row[7])} for row in rows]


def import_products(tenant_id: str, user_id: str, rows: list[dict[str, Any]]) -> dict[str, object]:
    """Validate a complete retailer upload before making catalog changes."""
    if not rows:
        raise ValueError("empty_product_import")
    prepared: list[tuple[str, str, str, int, int, str | None, str]] = []
    seen_skus: set[str] = set()
    seen_barcodes: set[str] = set()
    for index, row in enumerate(rows, start=1):
        sku, name, unit = str(row.get("sku", "")).strip(), str(row.get("name", "")).strip(), str(row.get("unit", "each")).strip()
        barcode = str(row.get("barcode", "")).strip() or None
        try:
            price_cents, available = int(row.get("price_cents", -1)), int(row.get("available", -1))
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid_import_row_{index}") from error
        if not sku or not name or not unit or price_cents < 0 or available < 0 or sku in seen_skus or (barcode is not None and barcode in seen_barcodes):
            raise ValueError(f"invalid_import_row_{index}")
        seen_skus.add(sku)
        if barcode is not None: seen_barcodes.add(barcode)
        prepared.append((f"product-{uuid.uuid4()}", sku, name, price_cents, available, barcode, unit))
    import_id = f"import-{uuid.uuid4()}"
    with connect() as connection, connection.cursor() as cursor:
        for _, sku, _, _, _, barcode, _ in prepared:
            cursor.execute("SELECT 1 FROM demo_products WHERE tenant_id = %s AND (sku = %s OR (%s IS NOT NULL AND barcode = %s))", (tenant_id, sku, barcode, barcode))
            if cursor.fetchone() is not None:
                raise ValueError("duplicate_sku_or_barcode")
        for product_id, sku, name, price_cents, available, barcode, unit in prepared:
            cursor.execute("INSERT INTO demo_products (tenant_id, product_id, sku, name, price_cents, available, barcode, unit, lifecycle_status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active')", (tenant_id, product_id, sku, name, price_cents, available, barcode, unit))
        cursor.execute("INSERT INTO kvr_product_imports (tenant_id, import_id, submitted_by, row_count, status) VALUES (%s,%s,%s,%s,'accepted')", (tenant_id, import_id, user_id, len(prepared)))
    return {"import_id": import_id, "status": "accepted", "imported_count": len(prepared)}
