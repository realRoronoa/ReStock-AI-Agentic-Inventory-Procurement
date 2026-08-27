"""Settings schema.

**Read-only, and deliberately so.** Spend limits arrive from the server
environment. Exposing a writable settings endpoint would let any client raise its
own spending cap, which would make the guardrails decorative — the frontend is
untrusted, and a limit a client can change is not a limit.

Contains no secret. Integration state is reported as booleans only.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.money import paise_to_rupees


class GuardrailSettings(BaseModel):
    """The active spend ceilings. Configured via environment variables only."""

    max_reorder_quantity: int
    max_order_spend_paise: int
    max_order_spend: Decimal
    max_daily_spend_paise: int
    max_daily_spend: Decimal
    spend_day_timezone: str = Field(
        description="IANA timezone whose calendar day defines the daily cap."
    )


class IntegrationSettings(BaseModel):
    """Which external integrations are usable. Booleans only, never values."""

    razorpayx_payouts_configured: bool = Field(
        description="False means POST /api/orders/{id}/approve returns 503."
    )
    razorpayx_webhooks_configured: bool = Field(
        description="False means every webhook is refused with 401 (fails closed)."
    )
    llm_configured: bool = Field(
        description=(
            "False means POST /api/proposals/product/{id} returns 503. No "
            "recommendation is ever invented."
        )
    )
    payout_mode: str = Field(description="IMPS, NEFT or RTGS.")
    payout_purpose: str
    llm_model: str = Field(description="Model identifier. Not a credential.")


class ForecastSettings(BaseModel):
    forecast_history_days: int = Field(
        description="Days of sales history shown to the forecast agent."
    )


class SettingsRead(BaseModel):
    """Server configuration a client may safely see."""

    app_name: str
    environment: str
    version: str

    guardrails: GuardrailSettings
    integrations: IntegrationSettings
    forecast: ForecastSettings

    editable: bool = Field(
        default=False,
        description=(
            "Always false. These values come from the server environment. There "
            "is no endpoint to change them, because a client that could raise "
            "its own spend cap would make the guardrails meaningless."
        ),
    )

    @classmethod
    def from_config(cls, config, *, version: str) -> "SettingsRead":
        from app.core.money import PAISE_PER_RUPEE

        order_paise = config.MAX_ORDER_SPEND_INR * PAISE_PER_RUPEE
        daily_paise = config.MAX_DAILY_SPEND_INR * PAISE_PER_RUPEE

        return cls(
            app_name=config.APP_NAME,
            environment=config.ENVIRONMENT,
            version=version,
            guardrails=GuardrailSettings(
                max_reorder_quantity=config.MAX_REORDER_QUANTITY,
                max_order_spend_paise=order_paise,
                max_order_spend=paise_to_rupees(order_paise),
                max_daily_spend_paise=daily_paise,
                max_daily_spend=paise_to_rupees(daily_paise),
                spend_day_timezone=config.SPEND_DAY_TIMEZONE,
            ),
            integrations=IntegrationSettings(
                razorpayx_payouts_configured=config.razorpayx_configured,
                razorpayx_webhooks_configured=(
                    config.webhook_verification_configured
                ),
                llm_configured=config.llm_configured,
                payout_mode=config.RAZORPAY_PAYOUT_MODE,
                payout_purpose=config.RAZORPAY_PAYOUT_PURPOSE,
                llm_model=config.OPENAI_MODEL,
            ),
            forecast=ForecastSettings(
                forecast_history_days=config.FORECAST_HISTORY_DAYS
            ),
        )
