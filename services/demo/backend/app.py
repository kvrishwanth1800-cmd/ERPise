"""Demo-mode HTTP adapter with authenticated, tenant-scoped durable workflows."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psycopg
from kafka import KafkaConsumer, KafkaProducer

DATABASE_URL = os.environ["DATABASE_URL"]
KAFKA_BOOTSTRAP_SERVERS = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
DEMO_MODE = os.environ.get("DEMO_MODE") == "true"
DEMO_EMAIL = os.environ.get("DEMO_USER_EMAIL", "")
DEMO_PASSWORD = os.environ.get("DEMO_USER_PASSWORD", "")
TENANT_ID = "demo-tenant"
USER_ID = "demo-admin"
ROLE = "administrator"
TOPIC = "program-a.events.v1"


def migrate_and_seed() -> None:
    """Apply lexical migrations and idempotently create the governed demo tenant."""
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            for migration in sorted(Path("/app/migrations").glob("*.sql")):
                cursor.execute(migration.read_text())
            cursor.execute(
                """INSERT INTO demo_products
                (tenant_id, product_id, sku, name, price_cents, available)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, product_id) DO NOTHING""",
                (TENANT_ID, "coffee", "DEMO-COFFEE", "Demo Coffee", 499, 100),
            )
            cursor.execute(
                """INSERT INTO demo_customers (tenant_id, customer_id, email, consented)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (tenant_id, customer_id) DO NOTHING""",
                (TENANT_ID, "customer", "customer@erpise.local"),
            )


def encode_event(event: dict[str, Any]) -> bytes:
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode()


def publish_pending() -> None:
    """Publish committed outbox rows. Failed publication leaves a row pending."""
    try:
        producer = KafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS)
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT event_id, tenant_id, event_type, payload FROM demo_outbox "
                    "WHERE published_at IS NULL ORDER BY created_at FOR UPDATE SKIP LOCKED"
                )
                for event_id, tenant_id, event_type, payload in cursor.fetchall():
                    event = {
                        "version": "v1",
                        "event_id": event_id,
                        "tenant_id": tenant_id,
                        "event_type": event_type,
                        "payload": payload,
                    }
                    producer.send(TOPIC, encode_event(event), key=event_id.encode()).get(timeout=5)
                    cursor.execute(
                        "UPDATE demo_outbox SET published_at = now() WHERE event_id = %s",
                        (event_id,),
                    )
        producer.flush()
        producer.close()
    except Exception as error:
        print(json.dumps({"event": "outbox.publish.failed", "error": str(error)}), flush=True)


def apply_projection(event: dict[str, Any]) -> None:
    """Apply an idempotent order projection from a published Program A event."""
    if event["event_type"] != "order.created.v1":
        return
    payload = event["payload"]
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO demo_order_projection
                (tenant_id, order_id, fulfillment_status, event_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING""",
                (
                    event["tenant_id"],
                    payload["order_id"],
                    payload["fulfillment_status"],
                    event["event_id"],
                ),
            )


def consume_events() -> None:
    """Continuously maintain projections with consumer-safe event de-duplication."""
    while True:
        try:
            consumer = KafkaConsumer(
                TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="demo-projection-v1",
                auto_offset_reset="earliest",
                enable_auto_commit=True,
            )
            for message in consumer:
                apply_projection(json.loads(message.value.decode()))
        except Exception as error:
            print(json.dumps({"event": "projection.consumer.failed", "error": str(error)}), flush=True)
            time.sleep(2)


def session_from_request(handler: BaseHTTPRequestHandler) -> dict[str, str] | None:
    cookies = SimpleCookie(handler.headers.get("Cookie"))
    value = cookies.get("erpise_session")
    if value is None:
        return None
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT tenant_id, user_id, role FROM demo_sessions
                WHERE session_id = %s AND revoked_at IS NULL AND expires_at > now()""",
                (value.value,),
            )
            row = cursor.fetchone()
    if row is None:
        return None
    return {"session_id": value.value, "tenant_id": row[0], "user_id": row[1], "role": row[2]}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health/"):
            self.respond({"status": "ok"})
            return
        session = self.require_session()
        if session is None:
            return
        if self.path == "/api/session":
            self.respond({"tenant": session["tenant_id"], "user": session["user_id"], "role": session["role"]})
            return
        if self.path == "/api/products":
            self.products(session)
            return
        if self.path == "/api/reports/sales":
            self.sales_report(session)
            return
        if self.path == "/api/orders":
            self.orders(session)
            return
        self.respond({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/api/auth/login":
            self.login()
            return
        if self.path == "/api/auth/logout":
            self.logout()
            return
        session = self.require_session()
        if session is None:
            return
        if self.path == "/api/consent":
            self.consent(session)
            return
        if self.path == "/api/orders":
            self.checkout(session)
            return
        if self.path == "/api/cases":
            self.case(session)
            return
        self.respond({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def login(self) -> None:
        payload = self.payload()
        if not DEMO_MODE:
            self.respond({"error": "demo_login_disabled"}, HTTPStatus.FORBIDDEN)
            return
        valid = secrets.compare_digest(str(payload.get("email", "")), DEMO_EMAIL)
        valid = valid and secrets.compare_digest(str(payload.get("password", "")), DEMO_PASSWORD)
        if not valid:
            self.respond({"error": "invalid_credentials"}, HTTPStatus.UNAUTHORIZED)
            return
        session_id = secrets.token_urlsafe(32)
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO demo_sessions
                    (session_id, tenant_id, user_id, role, expires_at)
                    VALUES (%s, %s, %s, %s, %s)""",
                    (session_id, TENANT_ID, USER_ID, ROLE, datetime.now(UTC) + timedelta(hours=8)),
                )
        self.respond(
            {"tenant": TENANT_ID, "user": USER_ID, "role": ROLE},
            headers={"Set-Cookie": f"erpise_session={session_id}; HttpOnly; SameSite=Strict; Path=/"},
        )

    def logout(self) -> None:
        session = session_from_request(self)
        if session is not None:
            with psycopg.connect(DATABASE_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE demo_sessions SET revoked_at = now() WHERE session_id = %s",
                        (session["session_id"],),
                    )
        self.respond({}, headers={"Set-Cookie": "erpise_session=; Max-Age=0; Path=/"})

    def products(self, session: dict[str, str]) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT product_id, sku, name, price_cents, available FROM demo_products
                    WHERE tenant_id = %s ORDER BY sku""",
                    (session["tenant_id"],),
                )
                products = [
                    {"id": row[0], "sku": row[1], "name": row[2], "price_cents": row[3], "available": row[4]}
                    for row in cursor.fetchall()
                ]
        self.respond({"products": products})

    def consent(self, session: dict[str, str]) -> None:
        payload = self.payload()
        customer_id = str(payload.get("customer_id", "customer"))
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE demo_customers SET consented = TRUE WHERE tenant_id = %s AND customer_id = %s",
                    (session["tenant_id"], customer_id),
                )
        self.respond({"customer_id": customer_id, "consented": True})

    def checkout(self, session: dict[str, str]) -> None:
        payload = self.payload()
        product_id = str(payload.get("product_id", ""))
        quantity = int(payload.get("quantity", 0))
        customer_id = str(payload.get("customer_id", "customer"))
        fulfillment_method = str(payload.get("fulfillment_method", "pickup"))
        idempotency_key = self.headers.get("Idempotency-Key", "")
        if not product_id or quantity < 1 or not idempotency_key:
            self.respond({"error": "invalid_checkout"}, HTTPStatus.BAD_REQUEST)
            return
        order_id = f"order-{uuid.uuid4()}"
        payment_id = f"payment-{uuid.uuid4()}"
        event_id = f"event-{uuid.uuid4()}"
        try:
            with psycopg.connect(DATABASE_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """SELECT order_id FROM demo_orders
                        WHERE tenant_id = %s AND idempotency_key = %s""",
                        (session["tenant_id"], idempotency_key),
                    )
                    prior = cursor.fetchone()
                    if prior is not None:
                        self.respond({"order_id": prior[0], "idempotent": True})
                        return
                    cursor.execute(
                        """UPDATE demo_products SET available = available - %s
                        WHERE tenant_id = %s AND product_id = %s AND available >= %s
                        RETURNING price_cents""",
                        (quantity, session["tenant_id"], product_id, quantity),
                    )
                    if cursor.fetchone() is None:
                        raise ValueError("inventory_unavailable")
                    cursor.execute(
                        """INSERT INTO demo_orders
                        (tenant_id, order_id, customer_id, product_id, quantity, payment_id,
                         fulfillment_status, idempotency_key)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            session["tenant_id"], order_id, customer_id, product_id, quantity,
                            payment_id, f"{fulfillment_method}_promised", idempotency_key,
                        ),
                    )
                    event_payload = json.dumps(
                        {"order_id": order_id, "payment_id": payment_id,
                         "fulfillment_status": f"{fulfillment_method}_promised"}
                    )
                    cursor.execute(
                        """INSERT INTO demo_outbox (event_id, tenant_id, event_type, payload)
                        VALUES (%s, %s, %s, %s::jsonb)""",
                        (event_id, session["tenant_id"], "order.created.v1", event_payload),
                    )
        except ValueError as error:
            self.respond({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        publish_pending()
        self.respond(
            {"order_id": order_id, "payment_id": payment_id, "reservation": "confirmed",
             "fulfillment": f"{fulfillment_method}_promised"},
            HTTPStatus.CREATED,
        )

    def orders(self, session: dict[str, str]) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT order_id, fulfillment_status FROM demo_order_projection
                    WHERE tenant_id = %s ORDER BY order_id""",
                    (session["tenant_id"],),
                )
                orders = [{"order_id": row[0], "fulfillment_status": row[1]} for row in cursor.fetchall()]
        self.respond({"orders": orders})

    def sales_report(self, session: dict[str, str]) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT count(*), coalesce(sum(quantity), 0) FROM demo_orders
                    WHERE tenant_id = %s""",
                    (session["tenant_id"],),
                )
                orders, units = cursor.fetchone()
        self.respond({"orders": orders, "units": units})

    def case(self, session: dict[str, str]) -> None:
        self.respond({"status": "accepted", "tenant": session["tenant_id"]}, HTTPStatus.CREATED)

    def require_session(self) -> dict[str, str] | None:
        session = session_from_request(self)
        if session is None:
            self.respond({"error": "authentication_required"}, HTTPStatus.UNAUTHORIZED)
        return session

    def payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    def respond(
        self,
        body: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        print(json.dumps({"message": format % args}), flush=True)


if __name__ == "__main__":
    migrate_and_seed()
    threading.Thread(target=consume_events, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
