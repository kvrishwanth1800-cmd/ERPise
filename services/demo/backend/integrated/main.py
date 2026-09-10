"""Governed entry point that protects the legacy demo adapters at the boundary."""

from __future__ import annotations

import threading

import app
from foundation.access import AuthorizationDeniedError
from foundation.organization import ScopeDeniedError

from integrated.auth import FoundationSessionAuthorizer

AUTHORIZER = FoundationSessionAuthorizer()


class GovernedHandler(app.Handler):
    """Applies Foundation session revocation, tenant scope, and route authorization."""

    _actions = {
        "/api/session": "session.read",
        "/api/products": "products.read",
        "/api/reports/sales": "reports.read",
        "/api/orders": "orders.read",
        "/api/consent": "consent.write",
        "/api/cases": "cases.write",
    }

    def require_session(self) -> dict[str, str] | None:
        session = app.session_from_request(self)
        if session is None:
            self.respond({"error": "authentication_required"}, app.HTTPStatus.UNAUTHORIZED)
            return None
        action = self._actions.get(self.path)
        if self.path == "/api/orders" and self.command == "POST":
            action = "orders.write"
        if action is None:
            self.respond({"error": "not_found"}, app.HTTPStatus.NOT_FOUND)
            return None
        try:
            AUTHORIZER.authorize(session, action)
        except (AuthorizationDeniedError, ScopeDeniedError):
            self.respond({"error": "authorization_denied"}, app.HTTPStatus.FORBIDDEN)
            return None
        return session

    def logout(self) -> None:
        session = app.session_from_request(self)
        if session is not None:
            AUTHORIZER.revoke(session)
        super().logout()


if __name__ == "__main__":
    app.migrate_and_seed()
    threading.Thread(target=app.consume_events, daemon=True).start()
    app.ThreadingHTTPServer(("0.0.0.0", 8080), GovernedHandler).serve_forever()
