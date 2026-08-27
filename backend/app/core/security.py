"""Cryptographic verification of inbound webhooks, and secret hygiene.

The webhook endpoint is the one place where an unauthenticated stranger can try
to change order state and inventory. Everything here exists to make that safe.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Mapping

from app.core.errors import WebhookSignatureError

logger = logging.getLogger("restock.security")

#: Razorpay's signature header. Compared case-insensitively by Starlette.
RAZORPAY_SIGNATURE_HEADER = "X-Razorpay-Signature"

#: Unique per webhook event, and stable across Razorpay's redelivery attempts —
#: which makes it the correct idempotency key for webhook processing.
RAZORPAY_EVENT_ID_HEADER = "X-Razorpay-Event-Id"

#: Header names that must never reach a log record.
SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-razorpay-signature",
        "cookie",
        "set-cookie",
    }
)


def compute_webhook_signature(raw_body: bytes, secret: str) -> str:
    """HMAC-SHA256 of the **raw** request body, keyed by the webhook secret.

    Per Razorpay's documentation the message is the raw body exactly as
    received. Parsing the JSON and re-serialising it would change byte-level
    details — key order, whitespace, unicode escaping, float formatting — and
    produce a different digest for an authentic request.
    """
    return hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()


def verify_webhook_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str,
) -> None:
    """Raise `WebhookSignatureError` unless the signature is authentic.

    Fails closed in three distinct ways, all reported identically to the caller
    so a prober learns nothing about which precondition failed:

    * no secret configured on this server — an unverifiable webhook is refused
      rather than trusted, because accepting it would let anyone move stock;
    * no signature header present;
    * signature present but wrong.
    """
    if not secret:
        logger.error(
            "Rejected webhook: RAZORPAY_WEBHOOK_SECRET is not configured, so "
            "authenticity cannot be established."
        )
        raise WebhookSignatureError(
            "This server cannot verify webhook signatures, so the webhook was "
            "refused."
        )

    if not signature:
        logger.warning("Rejected webhook: missing %s header.", RAZORPAY_SIGNATURE_HEADER)
        raise WebhookSignatureError("Missing webhook signature header.")

    expected = compute_webhook_signature(raw_body, secret)

    # Constant-time comparison: a byte-by-byte early exit would leak the
    # expected digest one character at a time under a timing attack.
    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "Rejected webhook: signature mismatch (body length %d).", len(raw_body)
        )
        raise WebhookSignatureError()


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy of `headers` with credential-bearing values masked, for logging."""
    return {
        name: ("<redacted>" if name.lower() in SENSITIVE_HEADERS else value)
        for name, value in headers.items()
    }


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Render an identifier safely for logs: `rzp_test_ab...` .

    For key *ids*, which are not secret but are still worth not scattering
    around. Never call this on a key secret — do not log those at all.
    """
    if not value:
        return "<unset>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)}"


def assert_no_secrets(payload: Any, secrets: tuple[str, ...]) -> None:
    """Defensive check that no configured secret appears inside `payload`.

    Used on agent prompts. The LLM must never see payment credentials, and a
    prompt is assembled from enough moving parts that an assertion is cheaper
    than trusting review.
    """
    rendered = str(payload)
    for secret in secrets:
        if secret and secret in rendered:
            raise RuntimeError(
                "Refusing to proceed: a configured secret was found in content "
                "destined for an external model."
            )
