"""RazorpayX payment provider tests.

No test here contacts RazorpayX. The real provider is exercised against an
`httpx.MockTransport`, which lets the actual request-building and
response-interpretation code run while asserting on the exact bytes that would
have gone over the wire — including the idempotency header and the paise amount.

The most important assertions are the negative ones: missing credentials must
raise rather than pretend, and a timeout must not be reported as a failure.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import (
    PaymentNotConfiguredError,
    PaymentProviderError,
    PaymentTimeoutError,
)
from app.schemas.payment import (
    PayoutInstruction,
    PayoutMode,
    PayoutState,
    normalise_payout_status,
)
from app.services.payment_service import (
    MINIMUM_PAYOUT_PAISE,
    RazorpayXPaymentProvider,
    build_narration,
    build_reference_id,
    new_idempotency_key,
)

CONFIGURED = Settings(
    RAZORPAY_KEY_ID="rzp_test_dummy",
    RAZORPAY_KEY_SECRET="dummy_secret_value",
    RAZORPAY_ACCOUNT_NUMBER="2323230099089860",
    RAZORPAY_PAYOUT_MODE="IMPS",
    RAZORPAY_PAYOUT_PURPOSE="vendor bill",
)

SUCCESS_RESPONSE = {
    "id": "pout_R7ambiUdUvg6AD",
    "entity": "payout",
    "fund_account_id": "fa_TESTFUNDACCOUNT01",
    "amount": 360_000,
    "currency": "INR",
    "fees": 590,
    "tax": 90,
    "status": "queued",
    "purpose": "vendor bill",
    "utr": None,
    "mode": "IMPS",
    "reference_id": "restock-order-1",
    "created_at": 1755693656,
    "status_details": {"reason": "queued", "description": "Queued.", "source": "business"},
}


def _instruction(**overrides) -> PayoutInstruction:
    defaults = dict(
        order_id=1,
        fund_account_id="fa_TESTFUNDACCOUNT01",
        amount_paise=360_000,
        idempotency_key="11111111-2222-3333-4444-555555555555",
        mode=PayoutMode.IMPS,
        purpose="vendor bill",
        reference_id="restock-order-1",
        narration="Milk order 1",
        notes={"order_id": "1"},
    )
    defaults.update(overrides)
    return PayoutInstruction(**defaults)


def _provider(handler, config: Settings = CONFIGURED) -> RazorpayXPaymentProvider:
    return RazorpayXPaymentProvider(
        config, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


# --- configuration ----------------------------------------------------------


def test_missing_credentials_raise_and_send_nothing() -> None:
    """No fake success. Ever."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    provider = _provider(handler, config=Settings())

    with pytest.raises(PaymentNotConfiguredError) as exc:
        provider.create_payout(_instruction())

    assert called is False, "no request may be sent without credentials"
    assert exc.value.status_code == 503
    assert set(exc.value.details["missing_settings"]) == {
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_ACCOUNT_NUMBER",
    }


def test_configuration_error_names_variables_not_values() -> None:
    config = Settings(
        RAZORPAY_KEY_ID="rzp_test_dummy", RAZORPAY_KEY_SECRET="super_secret_value"
    )
    provider = RazorpayXPaymentProvider(config)

    with pytest.raises(PaymentNotConfiguredError) as exc:
        provider.ensure_configured()

    assert "RAZORPAY_ACCOUNT_NUMBER" in exc.value.details["missing_settings"]
    assert "super_secret_value" not in str(exc.value.to_payload())


def test_partial_configuration_still_fails() -> None:
    config = Settings(
        RAZORPAY_KEY_ID="rzp_test_dummy",
        RAZORPAY_KEY_SECRET="dummy",
        RAZORPAY_ACCOUNT_NUMBER="",
    )

    with pytest.raises(PaymentNotConfiguredError):
        RazorpayXPaymentProvider(config).ensure_configured()


# --- successful payout ------------------------------------------------------


def test_successful_payout_is_normalised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    outcome = _provider(handler).create_payout(_instruction())

    assert outcome.payout_id == "pout_R7ambiUdUvg6AD"
    assert outcome.raw_status == "queued"
    assert outcome.state is PayoutState.PENDING
    assert outcome.fees_paise == 590
    assert outcome.provider == "razorpayx"


def test_request_matches_the_documented_razorpayx_contract() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    _provider(handler).create_payout(_instruction())

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/payouts")

    body = captured["body"]
    assert body["account_number"] == "2323230099089860"
    assert body["fund_account_id"] == "fa_TESTFUNDACCOUNT01"
    assert body["amount"] == 360_000  # integer paise, never a float
    assert isinstance(body["amount"], int)
    assert body["currency"] == "INR"
    assert body["mode"] == "IMPS"
    assert body["purpose"] == "vendor bill"
    assert body["reference_id"] == "restock-order-1"
    assert body["narration"] == "Milk order 1"
    assert body["queue_if_low_balance"] is False


def test_idempotency_header_is_sent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("x-payout-idempotency")
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    _provider(handler).create_payout(
        _instruction(idempotency_key="abc-123-def-456")
    )

    assert captured["key"] == "abc-123-def-456"


def test_basic_auth_is_used_and_secret_is_not_in_the_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["raw_body"] = request.content.decode()
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    _provider(handler).create_payout(_instruction())

    assert captured["auth"].startswith("Basic ")
    assert "dummy_secret_value" not in captured["raw_body"]


def test_same_idempotency_key_is_reused_across_retries() -> None:
    """The key is an input, not generated inside the provider.

    Generating a fresh key per call is exactly the bug that produces duplicate
    payouts, so the provider must never mint one.
    """
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["x-payout-idempotency"])
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    provider = _provider(handler)
    instruction = _instruction(idempotency_key="stable-key-0001")
    provider.create_payout(instruction)
    provider.create_payout(instruction)

    assert keys == ["stable-key-0001", "stable-key-0001"]


def test_new_idempotency_keys_are_unique_uuids() -> None:
    import uuid

    keys = {new_idempotency_key() for _ in range(100)}

    assert len(keys) == 100
    uuid.UUID(next(iter(keys)))  # parses as a UUID, as the API requires


# --- provider errors --------------------------------------------------------


def test_api_error_is_surfaced_with_its_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": "Insufficient balance to process the payout.",
                }
            },
        )

    with pytest.raises(PaymentProviderError) as exc:
        _provider(handler).create_payout(_instruction())

    assert "Insufficient balance" in exc.value.message
    assert "No payout was created" in exc.value.message
    assert exc.value.details["razorpay_error_code"] == "BAD_REQUEST_ERROR"
    assert exc.value.status_code == 502


def test_server_error_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    with pytest.raises(PaymentProviderError):
        _provider(handler).create_payout(_instruction())


def test_response_without_a_payout_id_is_rejected() -> None:
    """Accepted but untrackable is not success."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entity": "payout", "status": "queued"})

    with pytest.raises(PaymentProviderError) as exc:
        _provider(handler).create_payout(_instruction())

    assert "no payout id" in exc.value.message


def test_unparseable_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with pytest.raises(PaymentProviderError):
        _provider(handler).create_payout(_instruction())


def test_connection_error_reports_that_nothing_was_created() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(PaymentProviderError) as exc:
        _provider(handler).create_payout(_instruction())

    assert "No payout was created" in exc.value.message


# --- the timeout case, which matters most -----------------------------------


def test_timeout_is_not_reported_as_a_failure() -> None:
    """A lost response is not evidence that the money did not move.

    This must be a distinct error type, because treating it as a failure would
    invite a retry that creates a second payout.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(PaymentTimeoutError) as exc:
        _provider(handler).create_payout(_instruction())

    assert exc.value.status_code == 504
    assert "unknown" in exc.value.message.lower()
    assert "not be retried automatically" in exc.value.message.lower()
    assert "failed" not in exc.value.message.lower()


# --- amount validation ------------------------------------------------------


def test_amount_below_the_razorpay_minimum_is_rejected_locally() -> None:
    """Caught here so the merchant gets a clear reason, not an opaque 400."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=SUCCESS_RESPONSE)

    with pytest.raises(PaymentProviderError) as exc:
        _provider(handler).create_payout(_instruction(amount_paise=99))

    assert called is False
    assert exc.value.details["minimum_paise"] == MINIMUM_PAYOUT_PAISE


def test_amount_exactly_at_the_minimum_is_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**SUCCESS_RESPONSE, "amount": 100})

    outcome = _provider(handler).create_payout(_instruction(amount_paise=100))

    assert outcome.payout_id


def test_zero_or_negative_amount_cannot_even_be_constructed() -> None:
    from pydantic import ValidationError

    for amount in (0, -1):
        with pytest.raises(ValidationError):
            _instruction(amount_paise=amount)


# --- status normalisation ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("queued", PayoutState.PENDING),
        ("pending", PayoutState.PENDING),
        ("processing", PayoutState.PENDING),
        ("initiated", PayoutState.PENDING),
        ("processed", PayoutState.PROCESSED),
        ("failed", PayoutState.FAILED),
        ("rejected", PayoutState.FAILED),
        ("cancelled", PayoutState.FAILED),
        ("reversed", PayoutState.REVERSED),
    ],
)
def test_documented_statuses_map_correctly(raw: str, expected: PayoutState) -> None:
    assert normalise_payout_status(raw) is expected


@pytest.mark.parametrize("raw", [None, "", "some_new_status_razorpay_invented"])
def test_unknown_status_never_maps_to_success(raw) -> None:
    """A status this system does not recognise must not be read as settled."""
    assert normalise_payout_status(raw) is PayoutState.UNKNOWN


def test_status_normalisation_is_case_insensitive() -> None:
    assert normalise_payout_status("PROCESSED") is PayoutState.PROCESSED


# --- field construction -----------------------------------------------------


def test_narration_is_stripped_to_the_allowed_charset() -> None:
    """RazorpayX allows alphanumerics and spaces only, max 30 characters."""
    narration = build_narration("Coffee Beans (Arabica) — 100% !", 42)

    assert len(narration) <= 30
    assert all(char.isalnum() or char == " " for char in narration)
    assert "order 42" in narration


def test_narration_survives_a_name_with_no_usable_characters() -> None:
    narration = build_narration("!!! ###", 7)

    assert narration == "ReStock order 7"
    assert all(char.isalnum() or char == " " for char in narration)


def test_narration_is_truncated_for_a_long_product_name() -> None:
    narration = build_narration("Extremely Long Product Name That Overflows", 12345)

    assert len(narration) <= 30


def test_reference_id_is_within_the_length_limit() -> None:
    reference = build_reference_id(999_999_999)

    assert len(reference) <= 40
    assert reference.startswith("restock-order-")
