"""RazorpayX payout creation.

This module moves real money. Everything about it is deliberately boring:
no LLM, no inference, no fallback that invents success.

Hard rules
----------
* **Nothing under `app/agents/` may import this module.** Enforced by a test
  (`tests/test_architecture.py`) rather than by convention.
* **No fake success, ever.** If credentials are absent the call raises. There is
  no code path in which this module reports a payout that did not happen.
* **Secrets never leave.** The key secret is passed to httpx as Basic auth
  credentials and is never logged, never returned, never placed in an audit row,
  and never included in an exception message.

API contract implemented (per RazorpayX documentation)
------------------------------------------------------
``POST {base}/payouts`` with HTTP Basic auth ``key_id:key_secret``, header
``X-Payout-Idempotency: <uuid>``, and a JSON body of ``account_number``,
``fund_account_id``, ``amount`` (integer paise, minimum 100), ``currency``
(``INR``), ``mode`` (``IMPS``/``NEFT``/``RTGS``), ``purpose``,
``queue_if_low_balance``, ``reference_id`` (≤40 chars) and ``narration``
(≤30 chars, alphanumeric and spaces only).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.config import Settings, settings as default_settings
from app.core.errors import (
    PaymentNotConfiguredError,
    PaymentProviderError,
    PaymentTimeoutError,
)
from app.core.money import format_inr
from app.core.security import mask_secret
from app.schemas.payment import (
    PayoutInstruction,
    PayoutMode,
    PayoutOutcome,
    normalise_payout_status,
)

logger = logging.getLogger("restock.payment")

#: RazorpayX rejects payouts below INR 1.00.
MINIMUM_PAYOUT_PAISE = 100

#: `narration` is restricted to alphanumerics and spaces by the API.
_NARRATION_ALLOWED = re.compile(r"[^A-Za-z0-9 ]")
_NARRATION_MAX_LENGTH = 30
_REFERENCE_ID_MAX_LENGTH = 40


def build_narration(product_name: str, order_id: int) -> str:
    """A bank-statement narration that satisfies RazorpayX's charset rules.

    Non-alphanumerics are stripped rather than replaced, and the result is
    truncated to 30 characters, so a product name containing punctuation cannot
    make an otherwise valid payout fail validation at the provider.
    """
    suffix = f" order {order_id}"
    room = max(0, _NARRATION_MAX_LENGTH - len(suffix))
    cleaned = _NARRATION_ALLOWED.sub("", product_name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)[:room].strip()
    narration = f"{cleaned}{suffix}" if cleaned else f"ReStock order {order_id}"
    return narration[:_NARRATION_MAX_LENGTH]


def build_reference_id(order_id: int) -> str:
    """Stable, human-greppable reference for reconciliation against RazorpayX."""
    return f"restock-order-{order_id}"[:_REFERENCE_ID_MAX_LENGTH]


def new_idempotency_key() -> str:
    """A fresh idempotency key.

    Called exactly once per order, at approval time, and persisted before the
    payout request goes out. Retries must reuse the stored value — generating a
    new key on retry is precisely the bug that creates duplicate payouts.
    """
    return str(uuid.uuid4())


@runtime_checkable
class PaymentProvider(Protocol):
    """What the approval service depends on.

    A Protocol rather than an ABC so the test double does not have to inherit
    from production code, and so nothing in `app/` needs to import a fake.
    """

    name: str

    def create_payout(self, instruction: PayoutInstruction) -> PayoutOutcome:
        """Create a payout, or raise.

        Raises:
            PaymentNotConfiguredError: credentials missing — nothing was sent.
            PaymentProviderError: the provider rejected the request.
            PaymentTimeoutError: no response. Outcome genuinely unknown.
        """
        ...


class RazorpayXPaymentProvider:
    """The real thing. Talks HTTP to RazorpayX."""

    name = "razorpayx"

    def __init__(
        self,
        config: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config or default_settings
        # An injected client is used by tests to mount a MockTransport. In
        # production this stays None and a client is created per call, so no
        # connection pool outlives a request.
        self._client = client

    # --- configuration -----------------------------------------------------

    def ensure_configured(self) -> None:
        """Fail loudly, and specifically, when the integration is incomplete."""
        missing = [
            name
            for name, value in (
                ("RAZORPAY_KEY_ID", self._config.RAZORPAY_KEY_ID),
                ("RAZORPAY_KEY_SECRET", self._config.RAZORPAY_KEY_SECRET),
                ("RAZORPAY_ACCOUNT_NUMBER", self._config.RAZORPAY_ACCOUNT_NUMBER),
            )
            if not value
        ]
        if missing:
            # Names of missing variables only — never their values.
            raise PaymentNotConfiguredError(
                "RazorpayX is not configured on this server, so no payout can "
                f"be created. Missing: {', '.join(missing)}. Nothing was "
                "charged and no payment was simulated.",
                details={"missing_settings": missing},
            )

    # --- request building --------------------------------------------------

    def _build_body(self, instruction: PayoutInstruction) -> dict[str, Any]:
        return {
            "account_number": self._config.RAZORPAY_ACCOUNT_NUMBER,
            "fund_account_id": instruction.fund_account_id,
            "amount": instruction.amount_paise,
            "currency": "INR",
            "mode": instruction.mode.value,
            "purpose": instruction.purpose,
            "queue_if_low_balance": self._config.RAZORPAY_QUEUE_IF_LOW_BALANCE,
            "reference_id": instruction.reference_id,
            "narration": instruction.narration,
            "notes": instruction.notes,
        }

    # --- the call ----------------------------------------------------------

    def create_payout(self, instruction: PayoutInstruction) -> PayoutOutcome:
        self.ensure_configured()

        if instruction.amount_paise < MINIMUM_PAYOUT_PAISE:
            # Caught here rather than at the provider so the merchant gets a
            # clear reason instead of an opaque upstream validation error.
            raise PaymentProviderError(
                f"Payout amount {format_inr(instruction.amount_paise)} is below "
                f"the RazorpayX minimum of {format_inr(MINIMUM_PAYOUT_PAISE)}.",
                details={
                    "amount_paise": instruction.amount_paise,
                    "minimum_paise": MINIMUM_PAYOUT_PAISE,
                },
            )

        url = f"{self._config.RAZORPAY_BASE_URL.rstrip('/')}/payouts"
        body = self._build_body(instruction)
        headers = {
            "Content-Type": "application/json",
            # The whole point: RazorpayX collapses repeated requests carrying
            # the same key onto a single payout.
            "X-Payout-Idempotency": instruction.idempotency_key,
        }

        logger.info(
            "payout_request order_id=%s amount_paise=%d mode=%s "
            "fund_account_id=%s key_id=%s idempotency_key=%s",
            instruction.order_id,
            instruction.amount_paise,
            instruction.mode.value,
            instruction.fund_account_id,
            mask_secret(self._config.RAZORPAY_KEY_ID),
            instruction.idempotency_key,
        )

        try:
            response = self._post(url, body, headers)
        except httpx.TimeoutException as exc:
            # CRITICAL: a timeout is not a failure. The request may well have
            # been accepted. Raising a distinct error keeps the caller from
            # marking the order failed or retrying with a new key.
            logger.error(
                "payout_timeout order_id=%s idempotency_key=%s — outcome UNKNOWN, "
                "not retrying automatically",
                instruction.order_id,
                instruction.idempotency_key,
            )
            raise PaymentTimeoutError(
                "The payout request to RazorpayX timed out. Its outcome is "
                "unknown: the payout may or may not have been created. It will "
                "NOT be retried automatically.",
                details={"order_id": instruction.order_id},
            ) from exc
        except httpx.RequestError as exc:
            # Connection-level failure: the request never completed. Treated as
            # a provider error rather than an unknown, but still never as a
            # payout failure decided by us.
            logger.error(
                "payout_transport_error order_id=%s type=%s",
                instruction.order_id,
                type(exc).__name__,
            )
            raise PaymentProviderError(
                "Could not reach RazorpayX to create the payout. No payout was "
                "created.",
                details={"order_id": instruction.order_id},
            ) from exc

        return self._interpret(response, instruction)

    def _post(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        auth = (self._config.RAZORPAY_KEY_ID, self._config.RAZORPAY_KEY_SECRET)
        timeout = self._config.RAZORPAY_TIMEOUT_SECONDS

        if self._client is not None:
            return self._client.post(
                url, json=body, headers=headers, auth=auth, timeout=timeout
            )

        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=body, headers=headers, auth=auth)

    def _interpret(
        self, response: httpx.Response, instruction: PayoutInstruction
    ) -> PayoutOutcome:
        if response.status_code >= 400:
            raise self._provider_error(response, instruction)

        try:
            payload = response.json()
        except ValueError as exc:
            raise PaymentProviderError(
                "RazorpayX returned a response that could not be parsed. The "
                "payout status is unclear; check the RazorpayX dashboard before "
                "retrying.",
                details={"order_id": instruction.order_id},
            ) from exc

        payout_id = payload.get("id")
        if not payout_id:
            raise PaymentProviderError(
                "RazorpayX accepted the request but returned no payout id, so "
                "the payout cannot be tracked.",
                details={"order_id": instruction.order_id},
            )

        raw_status = payload.get("status")
        outcome = PayoutOutcome(
            payout_id=payout_id,
            state=normalise_payout_status(raw_status),
            raw_status=raw_status,
            utr=payload.get("utr"),
            fees_paise=payload.get("fees"),
            tax_paise=payload.get("tax"),
            provider=self.name,
            # A deliberately narrow echo. The full body contains the fund
            # account and bank details; only what is needed for the audit
            # trail is kept.
            provider_metadata={
                "mode": payload.get("mode"),
                "purpose": payload.get("purpose"),
                "reference_id": payload.get("reference_id"),
                "created_at": payload.get("created_at"),
                "status_details": payload.get("status_details"),
            },
        )

        logger.info(
            "payout_created order_id=%s payout_id=%s razorpay_status=%s state=%s",
            instruction.order_id,
            outcome.payout_id,
            raw_status,
            outcome.state.value,
        )
        return outcome

    def _provider_error(
        self, response: httpx.Response, instruction: PayoutInstruction
    ) -> PaymentProviderError:
        """Turn an error response into a safe, useful exception.

        RazorpayX's `error.description` is written for merchants ("Insufficient
        balance", "Invalid fund account id") and is worth surfacing. The rest of
        the body is not, and the request — which contains the account number —
        is never echoed.
        """
        code: str | None = None
        description: str | None = None
        try:
            error = response.json().get("error", {})
            code = error.get("code")
            description = error.get("description")
        except ValueError:
            pass

        logger.error(
            "payout_rejected order_id=%s http_status=%s razorpay_code=%s",
            instruction.order_id,
            response.status_code,
            code,
        )

        message = "RazorpayX rejected the payout request"
        if description:
            message = f"{message}: {description}"
        message = f"{message}. No payout was created."

        return PaymentProviderError(
            message,
            details={
                "order_id": instruction.order_id,
                "http_status": response.status_code,
                "razorpay_error_code": code,
            },
        )


def get_payment_provider() -> PaymentProvider:
    """FastAPI dependency returning the configured payment provider.

    Exists so tests can override it with a double via
    `app.dependency_overrides`. There is exactly one production implementation:
    no environment variable can select a fake one.
    """
    return RazorpayXPaymentProvider()


def build_instruction(
    order,
    *,
    fund_account_id: str,
    idempotency_key: str,
    config: Settings | None = None,
) -> PayoutInstruction:
    """Assemble a payout instruction from authoritative order/database values.

    Called only by the approval service, and only after every guardrail has
    passed and the amount has been recomputed from the current supplier price.
    """
    config = config or default_settings
    return PayoutInstruction(
        order_id=order.id,
        fund_account_id=fund_account_id,
        amount_paise=order.amount_paise,
        idempotency_key=idempotency_key,
        mode=PayoutMode(config.RAZORPAY_PAYOUT_MODE),
        purpose=config.RAZORPAY_PAYOUT_PURPOSE,
        reference_id=build_reference_id(order.id),
        narration=build_narration(order.product.name, order.id),
        # Notes are echoed back on webhooks and visible in the RazorpayX
        # dashboard, which makes them the natural place for reconciliation
        # handles. Identifiers only — nothing sensitive.
        notes={
            "order_id": str(order.id),
            "product_id": str(order.product_id),
            "supplier_id": str(order.supplier_id),
            "quantity": str(order.quantity),
        },
    )
