# mypy: disable-error-code=call-overload
# ruff: noqa: E501
"""Tenant-scoped edge reservation service with idempotent lifecycle transitions."""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]


def connect() -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(DATABASE_URL)


def migrate() -> None:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute("CREATE TABLE IF NOT EXISTS demo_edge_reservations (tenant_id TEXT NOT NULL, reservation_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, quantity INTEGER NOT NULL CHECK (quantity > 0), status TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (tenant_id, reservation_id), UNIQUE (tenant_id, idempotency_key))")
        cursor.execute("CREATE TABLE IF NOT EXISTS demo_edge_capacity (tenant_id TEXT PRIMARY KEY, capacity INTEGER NOT NULL, reserved INTEGER NOT NULL)")
        cursor.execute("INSERT INTO demo_edge_capacity (tenant_id, capacity, reserved) VALUES ('demo-tenant', 10, 0) ON CONFLICT DO NOTHING")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.reply({"status": "ok"})
            return
        tenant = self.headers.get("X-Tenant-Id")
        if not tenant:
            self.reply({"error": "tenant_required"}, HTTPStatus.UNAUTHORIZED)
            return
        with connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT capacity, reserved FROM demo_edge_capacity WHERE tenant_id = %s", (tenant,))
            row = cursor.fetchone()
        self.reply({"tenant_id": tenant, "capacity": 0 if row is None else int(cast(int | str, row[0])), "reserved": 0 if row is None else int(cast(int | str, row[1]))})

    def do_POST(self) -> None:
        tenant = self.headers.get("X-Tenant-Id")
        if not tenant:
            self.reply({"error": "tenant_required"}, HTTPStatus.UNAUTHORIZED)
            return
        data = self.data()
        reservation_id = str(data.get("reservation_id", ""))
        key = self.headers.get("Idempotency-Key", "")
        if self.path == "/reserve":
            quantity = int(cast(int | str, data.get("quantity", 0)))
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT reservation_id, status FROM demo_edge_reservations WHERE tenant_id = %s AND idempotency_key = %s", (tenant, key))
                existing = cursor.fetchone()
                if existing is not None:
                    self.reply({"reservation_id": str(existing[0]), "status": str(existing[1]), "idempotent": True})
                    return
                cursor.execute("UPDATE demo_edge_capacity SET reserved = reserved + %s WHERE tenant_id = %s AND reserved + %s <= capacity RETURNING reserved", (quantity, tenant, quantity))
                if cursor.fetchone() is None:
                    self.reply({"error": "capacity_unavailable"}, HTTPStatus.CONFLICT)
                    return
                cursor.execute("INSERT INTO demo_edge_reservations (tenant_id, reservation_id, idempotency_key, quantity, status, expires_at) VALUES (%s, %s, %s, %s, 'reserved', now() + interval '5 minutes')", (tenant, reservation_id, key, quantity))
            self.reply({"reservation_id": reservation_id, "status": "reserved"}, HTTPStatus.CREATED)
            return
        statuses = {"/confirm": "confirmed", "/release": "released", "/expire": "expired"}
        if self.path in statuses:
            status = statuses[self.path]
            with connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT quantity, status FROM demo_edge_reservations WHERE tenant_id = %s AND reservation_id = %s FOR UPDATE", (tenant, reservation_id))
                row = cursor.fetchone()
                if row is None:
                    self.reply({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return
                if str(row[1]) == status or str(row[1]) in {"released", "expired"}:
                    self.reply({"reservation_id": reservation_id, "status": str(row[1]), "idempotent": True})
                    return
                if status in {"released", "expired"}:
                    cursor.execute("UPDATE demo_edge_capacity SET reserved = reserved - %s WHERE tenant_id = %s", (int(cast(int | str, row[0])), tenant))
                cursor.execute("UPDATE demo_edge_reservations SET status = %s WHERE tenant_id = %s AND reservation_id = %s", (status, tenant, reservation_id))
            self.reply({"reservation_id": reservation_id, "status": status})
            return
        self.reply({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def data(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        return {} if size == 0 else cast(dict[str, object], json.loads(self.rfile.read(size).decode()))

    def reply(self, body: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        value = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        self.wfile.write(value)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    migrate()
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
