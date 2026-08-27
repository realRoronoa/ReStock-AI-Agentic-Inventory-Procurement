"""Application error taxonomy and the single API error contract.

Every error the API returns has the shape::

    {"error": {"code": "ORDER_ALREADY_APPROVED",
               "message": "Only proposed orders can be approved.",
               "details": {...}}}

`code` is a stable machine-readable identifier the frontend can branch on;
`message` is safe to show a human. `details` is optional structured context.

Rules:

* Nothing here ever carries a stack trace, a DSN, an API key, or a provider
  response body. Diagnostics go to the log; the client gets the code.
* Raising an `AppError` from a service is how business rules reject an
  operation. Routers do not decide status codes for business failures — the
  exception already knows its own.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected, client-facing failure."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


# --- 404 Not found ----------------------------------------------------------


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    default_message = "Resource not found."


class ProductNotFoundError(NotFoundError):
    code = "PRODUCT_NOT_FOUND"
    default_message = "Product not found."


class OrderNotFoundError(NotFoundError):
    code = "ORDER_NOT_FOUND"
    default_message = "Order not found."


class SupplierNotFoundError(NotFoundError):
    code = "SUPPLIER_NOT_FOUND"
    default_message = "Supplier not found."


class UnknownPayoutError(NotFoundError):
    code = "WEBHOOK_UNKNOWN_PAYOUT"
    default_message = "No local order matches this payout."


# --- 400 Invalid business operation -----------------------------------------


class BusinessRuleError(AppError):
    code = "BUSINESS_RULE_VIOLATION"
    status_code = 400
    default_message = "The requested operation is not allowed."


class ProductNotLowStockError(BusinessRuleError):
    code = "PRODUCT_NOT_LOW_STOCK"
    default_message = (
        "This product is at or above its reorder threshold, so no reorder "
        "proposal can be created for it."
    )


class NoSuppliersError(BusinessRuleError):
    code = "NO_SUPPLIERS"
    default_message = "This product has no suppliers to choose between."


class NoSalesHistoryError(BusinessRuleError):
    code = "NO_SALES_HISTORY"
    default_message = (
        "This product has no recorded sales, so demand cannot be forecast."
    )


class SupplierNotForProductError(BusinessRuleError):
    code = "SUPPLIER_NOT_FOR_PRODUCT"
    default_message = "That supplier does not supply this product."


class SupplierNotPayableError(BusinessRuleError):
    code = "SUPPLIER_NO_FUND_ACCOUNT"
    default_message = (
        "That supplier has no RazorpayX fund account, so it cannot be paid. "
        "Onboard the supplier's bank details first."
    )


class InsufficientStockError(BusinessRuleError):
    code = "INSUFFICIENT_STOCK"
    default_message = (
        "That sale is larger than the stock on hand. It is refused rather than "
        "clamped to zero, because absorbing the difference would corrupt the "
        "demand signal the forecast depends on."
    )


class MalformedWebhookError(BusinessRuleError):
    code = "WEBHOOK_MALFORMED"
    default_message = "Webhook body is not a payload this endpoint understands."


# --- 401 Unauthenticated ----------------------------------------------------


class WebhookSignatureError(AppError):
    code = "WEBHOOK_SIGNATURE_INVALID"
    status_code = 401
    default_message = "Webhook signature verification failed."


# --- 409 Conflict / invalid state -------------------------------------------


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = 409
    default_message = "The resource is not in a state that allows this operation."


class InvalidStateTransitionError(ConflictError):
    code = "INVALID_STATE_TRANSITION"
    default_message = "That state transition is not allowed."


class OrderNotProposedError(ConflictError):
    code = "ORDER_NOT_PROPOSED"
    default_message = "Only proposed orders can be approved."


class OrderNotRejectableError(ConflictError):
    code = "ORDER_NOT_REJECTABLE"
    default_message = "Only proposed orders can be rejected."


class PayoutAlreadyRequestedError(ConflictError):
    code = "PAYOUT_ALREADY_REQUESTED"
    default_message = (
        "A payout has already been requested for this order. It will not be "
        "requested again."
    )


class PayoutOutcomeUnknownError(ConflictError):
    code = "PAYOUT_OUTCOME_UNKNOWN"
    default_message = (
        "A payout was already sent for this order but its outcome is not yet "
        "known. It will not be retried automatically, because a lost response "
        "does not mean the money did not move."
    )


# --- 422 Guardrail violations ------------------------------------------------
#
# 422 rather than 400: the request was well formed and the caller did nothing
# wrong, but the resulting operation is outside a configured ceiling.


class GuardrailError(AppError):
    code = "GUARDRAIL_VIOLATION"
    status_code = 422
    default_message = "A spending guardrail rejected this operation."


class QuantityLimitError(GuardrailError):
    code = "QUANTITY_LIMIT_EXCEEDED"
    default_message = "The reorder quantity exceeds the configured maximum."


class OrderSpendLimitError(GuardrailError):
    code = "ORDER_SPEND_LIMIT_EXCEEDED"
    default_message = "The order total exceeds the per-order spend limit."


class DailySpendLimitError(GuardrailError):
    code = "DAILY_SPEND_LIMIT_EXCEEDED"
    default_message = "This order would exceed the daily spend limit."


# --- 422 Untrusted-model-output failures ------------------------------------


class AgentOutputError(AppError):
    """The LLM returned something the backend refuses to act on."""

    code = "AGENT_OUTPUT_INVALID"
    status_code = 422
    default_message = "The AI recommendation failed validation and was rejected."


class ForecastInvalidError(AgentOutputError):
    code = "FORECAST_INVALID"
    default_message = (
        "The demand forecast returned by the model failed validation and was "
        "rejected. No order was created."
    )


class SupplierSelectionInvalidError(AgentOutputError):
    code = "SUPPLIER_SELECTION_INVALID"
    default_message = (
        "The supplier recommendation returned by the model failed validation "
        "and was rejected. No order was created."
    )


# --- 502 / 503 / 504 External dependency failures ---------------------------


class ExternalServiceError(AppError):
    code = "EXTERNAL_SERVICE_ERROR"
    status_code = 502
    default_message = "An upstream service failed."


class ForecastUnavailableError(ExternalServiceError):
    code = "FORECAST_UNAVAILABLE"
    default_message = "The forecasting model could not be reached. No order was created."


class SupplierSelectionUnavailableError(ExternalServiceError):
    code = "SUPPLIER_SELECTION_UNAVAILABLE"
    default_message = (
        "The supplier-selection model could not be reached. No order was created."
    )


class PaymentProviderError(ExternalServiceError):
    code = "PAYMENT_PROVIDER_ERROR"
    default_message = "RazorpayX rejected the payout request."


class ConfigurationError(AppError):
    code = "NOT_CONFIGURED"
    status_code = 503
    default_message = "A required integration is not configured on this server."


class PaymentNotConfiguredError(ConfigurationError):
    code = "PAYMENT_NOT_CONFIGURED"
    default_message = (
        "RazorpayX credentials are not configured on this server, so no payout "
        "can be created. Nothing was charged and no payment was simulated."
    )


class AgentNotConfiguredError(ConfigurationError):
    code = "AGENT_NOT_CONFIGURED"
    default_message = (
        "No LLM provider is configured on this server, so recommendations "
        "cannot be generated."
    )


class PaymentTimeoutError(AppError):
    """A payout request timed out. The outcome is genuinely unknown."""

    code = "PAYMENT_TIMEOUT"
    status_code = 504
    default_message = (
        "The payout request timed out. Its outcome is unknown and it will NOT "
        "be retried automatically; a timeout is not proof that the payout "
        "failed."
    )
