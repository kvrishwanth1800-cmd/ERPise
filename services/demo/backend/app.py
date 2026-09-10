"""Small demonstration API that composes the existing Program A service package."""
from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEMO = {
    "tenant": {"id": "demo-tenant", "name": "ERPise Demo"},
    "user": {"email": "demo@erpise.local", "role": "administrator"},
    "store": {"id": "demo-store", "name": "Demo Store"},
    "warehouse": {"id": "demo-warehouse", "name": "Demo Warehouse"},
    "register": {"id": "demo-register", "name": "Register 1"},
    "product": {"id": "demo-product", "sku": "DEMO-COFFEE", "name": "Demo Coffee", "price": 4.99},
    "inventory": {"available": 100},
    "customer": {"id": "demo-customer", "consented": True},
    "supplier": {"id": "demo-supplier", "name": "Demo Supplier"},
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/health/live", "/health/ready"}:
            self.respond({"status": "ok", "classification": "demonstration-pre-production"})
        elif self.path == "/api/demo":
            self.respond(DEMO)
        elif self.path == "/api/products":
            self.respond([DEMO["product"]])
        elif self.path == "/api/reports/sales":
            self.respond({"sales": 1, "total": DEMO["product"]["price"]})
        else:
            self.respond({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path in {"/api/consent", "/api/orders", "/api/messages", "/api/cases"}:
            self.respond({"status": "accepted", "resource": self.path.rsplit("/", 1)[-1]}, HTTPStatus.CREATED)
        else:
            self.respond({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def respond(self, body: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        print(json.dumps({"message": format % args, "tenant": "demo-tenant"}), flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("PORT", "8080"))), Handler).serve_forever()
