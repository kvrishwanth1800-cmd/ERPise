# ruff: noqa: E501, E701, E702, I001
"""Governed modular HTTP entry point for the Program A demonstration runtime."""
from __future__ import annotations
import json
import secrets
import uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from foundation.access import AuthorizationDeniedError
from foundation.organization import ScopeDeniedError
from integrated import audit, cases, catalog, customers, edge, events, orders, projections, reports, sessions
from integrated.auth import FoundationSessionAuthorizer
from integrated.persistence import migrate

AUTHORIZER = FoundationSessionAuthorizer()
ACTIONS = {"/api/session":"session.read","/api/products":"products.read","/api/reports/sales":"reports.read","/api/orders":"orders.read","/api/projections/orders":"orders.read","/api/consent":"consent.write","/api/consent/revoke":"consent.write","/api/events/replay":"orders.write","/api/edge":"orders.read","/api/cases":"cases.write"}

class GovernedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/health/"):
            self.respond({"status":"ok"}); return
        session = self.require_session()
        if session is None: return
        tenant = session["tenant_id"]
        if self.path == "/api/session": self.respond({"tenant":tenant,"user":session["user_id"],"role":session["role"]})
        elif self.path == "/api/products": self.respond({"products":catalog.products(tenant)})
        elif self.path == "/api/orders": self.respond({"orders":orders.list_orders(tenant)})
        elif self.path == "/api/projections/orders": self.respond({"orders":projections.orders(tenant)})
        elif self.path == "/api/reports/sales": self.respond(reports.sales(tenant))
        elif self.path == "/api/edge": self.respond(edge.status(tenant))
        elif self.path == "/api/cases": self.respond({"cases":cases.list_cases(tenant)})
        else: self.respond({"error":"not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/api/auth/login": self.login(); return
        if self.path == "/api/auth/logout": self.logout(); return
        session = self.require_session()
        if session is None: return
        tenant, user = session["tenant_id"], session["user_id"]
        try:
            data = self.payload()
            if self.path == "/api/consent":
                result = customers.grant_consent(tenant, str(data.get("customer_id","customer"))); action="consent.granted"
            elif self.path == "/api/consent/revoke":
                result = customers.revoke_consent(tenant, str(data.get("customer_id","customer"))); action="consent.revoked"
            elif self.path == "/api/orders":
                result = orders.create(tenant, str(data.get("customer_id","customer")), str(data.get("product_id","")), int(data.get("quantity",0)), str(data.get("fulfillment_method","pickup")), self.headers.get("Idempotency-Key", "")); action="order.created"
            elif self.path == "/api/events/replay":
                result = {"replayed":events.replay()}; action="events.replayed"
            elif self.path == "/api/cases":
                result = cases.create(tenant, user, str(data.get("subject","Program A support")), str(data.get("details",""))); action="case.created"
            elif self.path.startswith("/api/cases/"):
                result = cases.transition(tenant, self.path.rsplit("/",1)[-1], str(data.get("status","")), None if data.get("assignee") is None else str(data["assignee"])); action=f"case.{result['status']}"
            elif self.path.startswith("/api/edge/"):
                operation = self.path.rsplit("/",1)[-1]
                if operation == "reserve": result = edge.reserve(tenant, str(data.get("reservation_id", uuid.uuid4())), int(data.get("quantity",0)), self.headers.get("Idempotency-Key", ""))
                elif operation in {"confirm","release","expire"}: result = edge.transition(tenant, operation, str(data.get("reservation_id","")))
                else: self.respond({"error":"not_found"}, HTTPStatus.NOT_FOUND); return
                action = f"edge.{operation}"
            else:
                self.respond({"error":"not_found"}, HTTPStatus.NOT_FOUND); return
            audit.record(tenant, user, action, str(result.get("case_id", result.get("reservation_id", result.get("order_id", result.get("customer_id", ""))))), self.trace_id())
            self.respond(result, HTTPStatus.CREATED if self.path in {"/api/orders","/api/cases","/api/edge/reserve"} else HTTPStatus.OK)
        except (TypeError, ValueError) as error: self.respond({"error":str(error)}, HTTPStatus.BAD_REQUEST)

    def login(self) -> None:
        data = self.payload()
        if not (secrets.compare_digest(str(data.get("email", "")), "demo@erpise.local") and secrets.compare_digest(str(data.get("password", "")), "demo-only-password")):
            self.respond({"error":"invalid_credentials"}, HTTPStatus.UNAUTHORIZED); return
        session = sessions.create(); self.respond({"tenant":session["tenant_id"],"user":session["user_id"],"role":session["role"]}, headers={"Set-Cookie":f"erpise_session={session['session_id']}; HttpOnly; SameSite=Strict; Path=/"})
    def logout(self) -> None:
        session = self.session()
        if session is not None: AUTHORIZER.revoke(session); sessions.revoke(session["session_id"])
        self.respond({}, headers={"Set-Cookie":"erpise_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/"})
    def require_session(self) -> dict[str,str] | None:
        session = self.session()
        if session is None: self.respond({"error":"authentication_required"},HTTPStatus.UNAUTHORIZED); return None
        action = "orders.write" if self.path == "/api/orders" and self.command == "POST" else ACTIONS.get("/api/edge" if self.path.startswith("/api/edge") else "/api/cases" if self.path.startswith("/api/cases") else self.path)
        if action is None: self.respond({"error":"not_found"},HTTPStatus.NOT_FOUND); return None
        try: AUTHORIZER.authorize(session, action)
        except (AuthorizationDeniedError, ScopeDeniedError): self.respond({"error":"authorization_denied"},HTTPStatus.FORBIDDEN); return None
        return session
    def session(self) -> dict[str,str] | None:
        cookie = SimpleCookie(self.headers.get("Cookie")); value=cookie.get("erpise_session"); return sessions.get(None if value is None else value.value)
    def payload(self) -> dict[str,Any]:
        length=int(self.headers.get("Content-Length","0")); return {} if length == 0 else cast(dict[str,Any],json.loads(self.rfile.read(length).decode()))
    def trace_id(self) -> str: return self.headers.get("Trace-Id",str(uuid.uuid4()))
    def respond(self, body: dict[str,Any], status: HTTPStatus=HTTPStatus.OK, headers: dict[str,str]|None=None) -> None:
        encoded=json.dumps(body).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(encoded)))
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers(); self.wfile.write(encoded)
    def log_message(self, format: str, *args: object) -> None: return
if __name__ == "__main__":
    migrate(); events.start_worker(); ThreadingHTTPServer(("0.0.0.0",8080),GovernedHandler).serve_forever()
