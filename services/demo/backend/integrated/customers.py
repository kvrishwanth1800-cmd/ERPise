"""Tenant-scoped customer consent adapter."""

from __future__ import annotations

from integrated.persistence import connect


def set_consent(tenant_id: str, customer_id: str, consented: bool) -> dict[str, object]:
    """Persist one customer's consent state inside the current tenant boundary."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """UPDATE demo_customers SET consented = %s
            WHERE tenant_id = %s AND customer_id = %s""",
            (consented, tenant_id, customer_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("customer_not_found")
    return {"customer_id": customer_id, "consented": consented}


def grant_consent(tenant_id: str, customer_id: str) -> dict[str, object]:
    """Persist customer consent inside the current tenant boundary."""
    return set_consent(tenant_id, customer_id, True)


def revoke_consent(tenant_id: str, customer_id: str) -> dict[str, object]:
    """Revoke customer consent inside the current tenant boundary."""
    return set_consent(tenant_id, customer_id, False)
