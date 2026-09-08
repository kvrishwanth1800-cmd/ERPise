"""Local fake provider webhook verification for payment orchestration.

Confirms provider callbacks are genuine before any payment state is treated
as final. Mirrors ADR-001 (verified-evidence-controls-state): unverified
redirects or client-supplied confirmations must never confirm payment
state. Only tokenized provider references and amounts pass through this
boundary; no cardholder data (PAN or CVV) is ever part of a callback.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCallback:
    callback_id: str
    tenant_id: str
    kind: str
    subject_id: str
    provider_reference: str
    amount_cents: int
    signature: str


class ProviderWebhookVerifier:
    """Verifies HMAC-signed callbacks from the local fake payment provider."""

    def __init__(self, shared_secret: str) -> None:
        if not shared_secret:
            raise ValueError("a shared secret is required to verify provider callbacks")
        self._shared_secret = shared_secret.encode("utf-8")

    def sign(self, callback: ProviderCallback) -> str:
        """Compute the expected signature for a callback payload."""
        return hmac.new(self._shared_secret, self._message(callback), hashlib.sha256).hexdigest()

    def verify(self, callback: ProviderCallback) -> bool:
        """Return True only when the callback signature matches its payload."""
        return hmac.compare_digest(self.sign(callback), callback.signature)

    @staticmethod
    def _message(callback: ProviderCallback) -> bytes:
        return "|".join(
            (
                callback.tenant_id,
                callback.kind,
                callback.subject_id,
                callback.provider_reference,
                str(callback.amount_cents),
            )
        ).encode("utf-8")
