# ruff: noqa: E501, I001
"""Governed modular HTTP entry point for the Program A demonstration runtime."""

from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from foundation.access import AuthorizationDeniedError
from foundation.organization import ScopeDeniedError

from integrated import audit, catalog, customers, orders, reports, sessions
from integrated.auth import FoundationSessionAuthorizer
from integrated.persistence import migrate


AUTHORIZER = FoundationSessionAuthorizer()
ACTIONS = {
    "/api/session": "session.read",
    "/api/products": "products.read",
    "/api/reports/sales": "reports.read",
    "/api/orders": "orders.read",
    "/api/consent": "consent.write",
}


class GovernedHandler(BaseHTTPRequestHandler):
    """Owns the HTTP boundary and delegates Program A work to small adapters."""

    def do_GET(self) -> None:
        if self.path.startswith("/health/"):
            self.respond({"status": "ok"})
            return
        session = self.require_session()
        if session is None:
            return
        if self.path == "/api/session":
            self.respond({"tenant": session["tenant_id"], "user": session["user_id"], "role": session["role"]})
        elif self.path == "/api/products":
            self.respond({"products": catalog.products(session["tenant_id"])})
        elif self.path == "/api/orders":
            self.respond({"orders": orders.list_orders(session["tenant_id"])})
        elif self.path == "/api/reports/sales":
            self.respond(reports.sales(session["tenant_id"]))
        else:
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
        try:
            if self.path == "/api/consent":
                result = customers.grant_consent(session["tenant_id"], str(self.payload().get("customer_id", "customer")))
                audit.record(session["tenant_id"], session["user_id"], "consent.granted", result["customer_id"], self.trace_id())
                self.respond(result)
            elif self.path == "/api/orders":
                payload = self.payload()
                result = orders.create(session["tenant_id"], str(payload.get("customer_id", "customer")),
                                       str(payload.get("product_id", "")), int(payload.get("quantity", 0)),
                                       str(payload.get("fulfillment_method", "pickup")),
                                       self.headers.get("Idempotency-Key", ""))
                audit.record(session["tenant_id"], session["user_id"], "order.created", result["order_id"], self.trace_id())
                self.respond(result, HTTPStatus.CREATED)
            else:
                self.respond({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except (TypeError, ValueError) as error:
            self.respond({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def login(self) -> None:
        payload = self.payload()
        if payload.get("email") != "demo@erpise.local" or payload.get("password") != "demo-only-password":
            self.respond({"error": "invalid_credentials"}, HTTPStatus.UNAUTHORIZED)
            return
        session = sessions.create()
        self.respond({"tenant": session["tenant_id"], "user": session["user_id"], "role": session["role"]},
                     headers={"Set-Cookie": f"erpise_session={session['session_id']}; HttpOnly; SameSite=Strict; Path=/"})

    def logout(self) -> None:
        session = self.session()
        if session is not None:
            AUTHORIZER.revoke(session)
            sessions.revoke(session["session_id"])
        self.respond({}, headers={"Set-Cookie": "erpise_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})

    def require_session(self) -> dict[str, str] | None:
        session = self.session()
        if session is None:
            self.respond({"error": "authentication_required"}, HTTPStatus.UNAUTHORIZED)
            return None
        action = ACTIONS.get(self.path)
        if self.path == "/api/orders" and self.command == "POST":
            action = "orders.write"
        if action is None:
            self.respond({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return None
        try:
            AUTHORIZER.authorize(session, action)
        except (AuthorizationDeniedError, ScopeDeniedError):
            self.respond({"error": "authorization_denied"}, HTTPStatus.FORBIDDEN)
            return None
        return session

    def session(self) -> dict[str, str] | None:
        cookies = SimpleCookie(self.headers.get("Cookie"))
        value = cookies.get("erpise_session")
        return sessions.get(None if value is None else value.value)

    def payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        decoded = json.loads(self.rfile.read(length).decode())
        return decoded if isinstance(decoded, dict) else {}

    def trace_id(self) -> str:
        return self.headers.get("Trace-Id", str(uuid.uuid4()))

    def respond(self, body: dict[str, Any], status: HTTPStatus = HTTPStatus.OK,
                headers: dict[str, str] | None = None) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    migrate()
    ThreadingHTTPServer(("0.0.0.0", 8080), GovernedHandler).serve_forever()
