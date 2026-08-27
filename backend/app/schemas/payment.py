"""Payment DTOs — the boundary between business logic and RazorpayX.

`PayoutInstruction` is what the backend decided to pay. `PayoutOutcome` is a
*normalised* view of what the provider said. Keeping both provider-agnostic
means the approval service never touches a RazorpayX-shaped dict, and swapping
provider would not ripple into business logic.

Note what `PayoutOutcome` does NOT contain: no order status. A provider does not
get to decide local state. The approval service maps an outcome onto the state
machine, and only a verified webhook can produce PAID.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import paise_to_rupees


class PayoutMode(str, Enum):
    """RazorpayX transfer rails. Values are case-sensitive per the API docs."""

    IMPS = "IMPS"
    NEFT = "NEFT"
    RTGS = "RTGS"


class PayoutState(str, Enum):
    """Normalised payout lifecycle, mapped from RazorpayX's own status strings.

    Deliberately coarser than RazorpayX's vocabulary, because the only thing
    the domain needs to know is: is this settled, still moving, or over?
    """

    #: Accepted, not settled. queued / pending / processing / initiated.
    PENDING = "pending"
    #: Money reached the supplier. The only state that may lead to PAID.
    PROCESSED = "processed"
    #: Money did not move. failed / rejected / cancelled.
    FAILED = "failed"
    #: Money moved and came back.
    REVERSED = "reversed"
    #: A status string this system does not recognise. Never treated as success.
    UNKNOWN = "unknown"


#: RazorpayX payout status -> normalised state.
#:
#: Anything absent maps to UNKNOWN, which is treated as "not settled". That
#: default matters: if Razorpay introduces a new status, this system must not
#: guess that it means success.
RAZORPAY_STATUS_MAP: dict[str, PayoutState] = {
    "queued": PayoutState.PENDING,
    "pending": PayoutState.PENDING,
    "processing": PayoutState.PENDING,
    "initiated": PayoutState.PENDING,
    "processed": PayoutState.PROCESSED,
    "failed": PayoutState.FAILED,
    "rejected": PayoutState.FAILED,
    "cancelled": PayoutState.FAILED,
    "reversed": PayoutState.REVERSED,
}


def normalise_payout_status(raw_status: str | None) -> PayoutState:
    if not raw_status:
        return PayoutState.UNKNOWN
    return RAZORPAY_STATUS_MAP.get(raw_status.strip().lower(), PayoutState.UNKNOWN)


class PayoutInstruction(BaseModel):
    """A fully-validated instruction to pay one supplier.

    Every field is derived from the database by the approval service. Nothing
    here can originate from a client or from an LLM.
    """

    model_config = ConfigDict(frozen=True)

    order_id: int
    fund_account_id: str = Field(min_length=1)
    amount_paise: int = Field(
        gt=0,
        description="Integer paise. RazorpayX enforces a minimum of 100 (INR 1.00).",
    )
    idempotency_key: str = Field(
        min_length=1,
        description=(
            "Stable across retries of the same logical payout. Sent as "
            "X-Payout-Idempotency."
        ),
    )
    mode: PayoutMode
    purpose: str
    reference_id: str = Field(max_length=40)
    narration: str = Field(max_length=30)
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def amount(self) -> Decimal:
        return paise_to_rupees(self.amount_paise)


class PayoutOutcome(BaseModel):
    """Normalised result of a payout creation call."""

    model_config = ConfigDict(frozen=True)

    payout_id: str = Field(description="RazorpayX payout id, e.g. pout_XXXXXXXXXXXX.")
    state: PayoutState
    raw_status: str | None = Field(
        default=None, description="RazorpayX status string, verbatim."
    )
    utr: str | None = Field(
        default=None, description="Bank reference; null until settled."
    )
    fees_paise: int | None = None
    tax_paise: int | None = None
    provider: str = Field(description="Which provider produced this outcome.")
    #: Small, non-sensitive echo of the response for the audit trail. Never the
    #: full body, and never anything credential-shaped.
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentRead(BaseModel):
    """Payment-side view of an order, for API responses."""

    payout_id: str | None = Field(
        default=None, description="Null until a payout has been created."
    )
    payout_status: str | None = Field(
        default=None, description="RazorpayX status, verbatim. Null before payout."
    )
    payout_requested: bool = Field(
        description="A payout request has been sent for this order."
    )
    outcome_unknown: bool = Field(
        description=(
            "A payout request was sent but no identifier came back. Needs "
            "reconciliation; the system will not retry automatically."
        )
    )
    awaiting_settlement: bool = Field(
        description="Payout created; waiting for a webhook to report the outcome."
    )
    failure_reason: str | None = None


class WebhookAck(BaseModel):
    """Response to a webhook delivery.

    Razorpay retries on any non-2xx, so this is returned with 200 for every
    outcome that was correctly handled — including "duplicate, ignored" and
    "intermediate status, no action". Retrying those would be pointless.

    Non-2xx is reserved for deliveries that genuinely should not be accepted:
    401 for a bad signature, 400 for a malformed body, 404 for an unknown
    payout, 409 for an event that contradicts local state.
    """

    received: bool = True
    event_id: str
    event_type: str
    outcome: str = Field(
        description=(
            "applied | already_applied | acknowledged | ignored — what "
            "processing did with this delivery."
        )
    )
    duplicate: bool = Field(description="True if this event had already been processed.")
    order_id: int | None = None
    order_status: str | None = Field(
        default=None, description="Local order status after processing."
    )
    detail: str
