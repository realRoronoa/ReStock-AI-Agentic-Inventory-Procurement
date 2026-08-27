"""Test doubles for every external service.

These live in `tests/` on purpose. Nothing in `app/` imports them and no
environment variable can select them, so there is no configuration mistake that
could put a fake payment or a fake forecast into a running server. A stubbed
recommendation in production would be indistinguishable from a real one in the
audit trail; a stubbed payout would be worse.

Each double satisfies the corresponding Protocol structurally, without
inheriting from production code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Callable

from app.agents.forecast_agent import ForecastRequest, ForecastResult
from app.agents.supplier_agent import SupplierSelection, SupplierSelectionRequest
from app.core.errors import (
    ForecastUnavailableError,
    PaymentProviderError,
    PaymentTimeoutError,
    SupplierSelectionUnavailableError,
)
from app.schemas.payment import PayoutInstruction, PayoutOutcome, PayoutState

# --- Forecast ---------------------------------------------------------------


class FakeForecastProvider:
    """Returns a scripted forecast, or raises a scripted error.

    `raw_override` bypasses the Pydantic model entirely so tests can feed the
    validation layer exactly what a misbehaving LLM would return — a negative
    quantity, a string, a missing key — which is the whole point of having a
    validation layer.
    """

    name = "fake-forecast"

    def __init__(
        self,
        *,
        quantity: int = 75,
        reasoning: str = "Recent daily demand averages 20 litres with weekend "
        "uplift; 75 covers the delivery window plus a small buffer.",
        raise_error: Exception | None = None,
        raw_override: dict[str, Any] | None = None,
    ) -> None:
        self.quantity = quantity
        self.reasoning = reasoning
        self.raise_error = raise_error
        self.raw_override = raw_override
        self.calls: list[ForecastRequest] = []

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        self.calls.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.raw_override is not None:
            # Validated exactly as the real provider validates it.
            return ForecastResult.model_validate(self.raw_override)
        return ForecastResult(
            recommended_quantity=self.quantity, reasoning=self.reasoning
        )


class TimingOutForecastProvider:
    name = "timing-out-forecast"

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        raise ForecastUnavailableError(
            "The forecasting model did not respond in time. No order was created."
        )


# --- Supplier selection -----------------------------------------------------


class FakeSupplierProvider:
    """Returns a scripted supplier choice.

    `supplier_id=None` means "pick the first option offered", which keeps tests
    that do not care about the choice from hard-coding ids.
    """

    name = "fake-supplier"

    def __init__(
        self,
        *,
        supplier_id: int | None = None,
        reasoning: str = "Chosen for the best balance of unit price against "
        "delivery time given the current shortfall.",
        raise_error: Exception | None = None,
        raw_override: dict[str, Any] | None = None,
    ) -> None:
        self.supplier_id = supplier_id
        self.reasoning = reasoning
        self.raise_error = raise_error
        self.raw_override = raw_override
        self.calls: list[SupplierSelectionRequest] = []

    def select(self, request: SupplierSelectionRequest) -> SupplierSelection:
        self.calls.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.raw_override is not None:
            return SupplierSelection.model_validate(self.raw_override)

        chosen = (
            self.supplier_id
            if self.supplier_id is not None
            else request.suppliers[0].supplier_id
        )
        return SupplierSelection(supplier_id=chosen, reasoning=self.reasoning)


class TimingOutSupplierProvider:
    name = "timing-out-supplier"

    def select(self, request: SupplierSelectionRequest) -> SupplierSelection:
        raise SupplierSelectionUnavailableError(
            "The supplier-selection model did not respond in time."
        )


# --- Payments ---------------------------------------------------------------


class FakePaymentProvider:
    """Records payout instructions and returns a scripted outcome.

    Also emulates RazorpayX idempotency: a second call carrying an
    idempotency key it has already seen returns the *original* payout rather
    than minting a new id. That makes "a duplicate request cannot create a
    second payout" a property the tests can actually assert, instead of an
    untested claim about the provider.
    """

    name = "fake-payment"

    def __init__(
        self,
        *,
        payout_id: str = "pout_TESTPAYOUT000001",
        state: PayoutState = PayoutState.PENDING,
        raw_status: str = "queued",
        raise_error: Exception | None = None,
        on_call: Callable[[PayoutInstruction], None] | None = None,
    ) -> None:
        self.payout_id = payout_id
        self.state = state
        self.raw_status = raw_status
        self.raise_error = raise_error
        # Hook that runs inside the payout call. Used to simulate a concurrent
        # second approval arriving while the first is still in flight.
        self.on_call = on_call
        self.instructions: list[PayoutInstruction] = []
        self._by_idempotency_key: dict[str, PayoutOutcome] = {}

    @property
    def call_count(self) -> int:
        return len(self.instructions)

    @property
    def distinct_payout_ids(self) -> set[str]:
        return {outcome.payout_id for outcome in self._by_idempotency_key.values()}

    def create_payout(self, instruction: PayoutInstruction) -> PayoutOutcome:
        self.instructions.append(instruction)

        if self.on_call is not None:
            self.on_call(instruction)

        if self.raise_error is not None:
            raise self.raise_error

        existing = self._by_idempotency_key.get(instruction.idempotency_key)
        if existing is not None:
            # What RazorpayX does with a repeated X-Payout-Idempotency value.
            return existing

        outcome = PayoutOutcome(
            # Verbatim: a test that needs two distinct payouts sets a new
            # `payout_id` between calls, which also mirrors reality (RazorpayX
            # mints a fresh id per genuinely new payout).
            payout_id=self.payout_id,
            state=self.state,
            raw_status=self.raw_status,
            provider=self.name,
            provider_metadata={"fake": True},
        )
        self._by_idempotency_key[instruction.idempotency_key] = outcome
        return outcome


class FailingPaymentProvider:
    """Provider that rejects the request outright (money definitely not sent)."""

    name = "failing-payment"

    def __init__(self, message: str = "Insufficient balance.") -> None:
        self.message = message
        self.instructions: list[PayoutInstruction] = []

    def create_payout(self, instruction: PayoutInstruction) -> PayoutOutcome:
        self.instructions.append(instruction)
        raise PaymentProviderError(
            f"RazorpayX rejected the payout request: {self.message} "
            "No payout was created.",
            details={"order_id": instruction.order_id},
        )


class TimingOutPaymentProvider:
    """Provider whose response is lost. Outcome genuinely unknown."""

    name = "timing-out-payment"

    def __init__(self) -> None:
        self.instructions: list[PayoutInstruction] = []

    def create_payout(self, instruction: PayoutInstruction) -> PayoutOutcome:
        self.instructions.append(instruction)
        raise PaymentTimeoutError(details={"order_id": instruction.order_id})


# --- Webhook payload construction -------------------------------------------

#: Secret used by webhook tests. Only ever a test value.
TEST_WEBHOOK_SECRET = "test_webhook_secret_do_not_use_anywhere"


def build_payout_webhook(
    event: str,
    payout_id: str,
    *,
    status: str | None = None,
    amount_paise: int = 360_000,
    utr: str | None = "523223155921",
    failure_reason: str | None = None,
    status_description: str | None = None,
) -> dict[str, Any]:
    """A payout webhook body shaped like the documented RazorpayX payload.

    Nesting matches the real thing (`payload.payout.entity.id`) so the tests
    exercise the same extraction path production does.
    """
    entity: dict[str, Any] = {
        "id": payout_id,
        "entity": "payout",
        "fund_account_id": "fa_TESTFUNDACCOUNT01",
        "amount": amount_paise,
        "currency": "INR",
        "fees": 0,
        "tax": 0,
        "status": status or event.split(".")[-1],
        "purpose": "vendor bill",
        "utr": utr,
        "mode": "IMPS",
        "reference_id": "restock-order-1",
        "narration": "Milk order 1",
        "batch_id": None,
        "failure_reason": failure_reason,
        "created_at": 1755693656,
        "status_details": {
            "reason": event.split(".")[-1],
            "description": status_description or f"Payout is {event.split('.')[-1]}.",
            "source": "beneficiary_bank",
        },
    }
    return {
        "entity": "event",
        "account_id": "acc_TESTACCOUNT0001",
        "event": event,
        "contains": ["payout"],
        "payload": {"payout": {"entity": entity}},
        "created_at": 1755693679,
    }


def sign_webhook(body: dict[str, Any] | bytes, secret: str = TEST_WEBHOOK_SECRET) -> tuple[bytes, str]:
    """Serialise (if needed) and sign a webhook body.

    Returns the exact bytes and their signature, so tests post the same bytes
    that were signed — mirroring the production requirement that the raw body is
    what gets verified.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()
    return raw, signature
