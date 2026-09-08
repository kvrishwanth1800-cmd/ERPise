from __future__ import annotations

from foundation.webhook_verifier import ProviderCallback, ProviderWebhookVerifier


def callback(signature: str = "") -> ProviderCallback:
    return ProviderCallback(
        callback_id="cb-1",
        tenant_id="tenant-a",
        kind="capture",
        subject_id="intent-1",
        provider_reference="prov-ref-1",
        amount_cents=1250,
        signature=signature,
    )


def test_verify_accepts_a_correctly_signed_callback() -> None:
    verifier = ProviderWebhookVerifier("shared-secret")
    signed = callback(verifier.sign(callback()))
    assert verifier.verify(signed)


def test_verify_rejects_a_tampered_payload() -> None:
    verifier = ProviderWebhookVerifier("shared-secret")
    signature = verifier.sign(callback())
    tampered = ProviderCallback(
        callback_id="cb-1",
        tenant_id="tenant-a",
        kind="capture",
        subject_id="intent-1",
        provider_reference="prov-ref-1",
        amount_cents=999999,
        signature=signature,
    )
    assert not verifier.verify(tampered)


def test_verify_rejects_a_signature_from_a_different_secret() -> None:
    signer = ProviderWebhookVerifier("shared-secret")
    verifier = ProviderWebhookVerifier("other-secret")
    signed = callback(signer.sign(callback()))
    assert not verifier.verify(signed)
