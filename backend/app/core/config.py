"""Application configuration.

All configuration enters the process here and nowhere else. Modules import the
`settings` singleton rather than reading `os.environ` directly, so that:

* every knob is declared and validated in one place;
* secrets have exactly one ingress point and are easy to audit;
* tests can build an isolated `Settings()` without mutating global state.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]

DEFAULT_SQLITE_FILENAME = "restock_ai.db"


class Settings(BaseSettings):
    """Typed view over the environment / `.env` file."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    APP_NAME: str = "ReStock AI"
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # --- Database ----------------------------------------------------------
    DATABASE_URL: str = f"sqlite:///{(BACKEND_DIR / DEFAULT_SQLITE_FILENAME).as_posix()}"

    # --- LLM provider ------------------------------------------------------
    # Kept deliberately generic: the agents depend on a provider interface, not
    # on OpenAI. Swapping providers should mean changing these values only.
    # Only "openai" (or any OpenAI-compatible endpoint via OPENAI_BASE_URL) is
    # implemented. There is deliberately no "fake" provider selectable at
    # runtime: a stubbed recommendation in production would be indistinguishable
    # from a real one in the audit trail. Fakes exist in the test suite only.
    LLM_PROVIDER: Literal["openai"] = "openai"
    LLM_TIMEOUT_SECONDS: float = 30.0
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # --- RazorpayX ---------------------------------------------------------
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_ACCOUNT_NUMBER: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    RAZORPAY_BASE_URL: str = "https://api.razorpay.com/v1"
    RAZORPAY_TIMEOUT_SECONDS: float = 30.0

    # --- Payout parameters -------------------------------------------------
    # RazorpayX payout mode. IMPS settles fastest for small amounts; NEFT and
    # RTGS are the documented alternatives. RTGS has a high minimum, so IMPS is
    # the sane default for grocery-scale procurement.
    RAZORPAY_PAYOUT_MODE: Literal["IMPS", "NEFT", "RTGS"] = "IMPS"
    # Must be one of RazorpayX's allowed purposes, or a custom purpose created
    # in the dashboard. "vendor bill" is what paying a supplier actually is.
    RAZORPAY_PAYOUT_PURPOSE: str = "vendor bill"
    # If true, RazorpayX queues the payout instead of failing it when the
    # source account balance is short. Left false so a funding problem surfaces
    # immediately rather than silently sitting in a queue.
    RAZORPAY_QUEUE_IF_LOW_BALANCE: bool = False

    # --- Spend guardrails --------------------------------------------------
    # Deterministic, LLM-inaccessible ceilings. See app/core/limits.py.
    MAX_ORDER_SPEND_INR: int = Field(default=10_000, gt=0)
    MAX_DAILY_SPEND_INR: int = Field(default=25_000, gt=0)
    MAX_REORDER_QUANTITY: int = Field(default=500, gt=0)

    # IANA timezone whose calendar day defines "today" for the daily spend cap.
    # A UTC day would roll over at 05:30 IST and split one trading day's
    # spending across two budgets.
    SPEND_DAY_TIMEZONE: str = "Asia/Kolkata"

    # --- Forecasting -------------------------------------------------------
    # Days of sales history shown to the forecast agent.
    FORECAST_HISTORY_DAYS: int = Field(default=28, gt=0)

    @field_validator("DATABASE_URL")
    @classmethod
    def _resolve_relative_sqlite_path(cls, value: str) -> str:
        """Anchor relative SQLite paths to `backend/`, not the process cwd.

        `sqlite:///./restock_ai.db` would otherwise create a different database
        depending on where uvicorn/pytest/alembic happened to be launched from.
        """
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            return value

        raw_path = value[len(prefix) :]
        if raw_path in ("", ":memory:"):
            return value

        path = Path(raw_path)
        if path.is_absolute():
            return value
        return f"{prefix}{(BACKEND_DIR / path).resolve().as_posix()}"

    # --- Derived helpers ---------------------------------------------------
    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def razorpayx_configured(self) -> bool:
        """True only when every value needed to create a payout is present.

        Used to fail loudly instead of pretending a payout happened.
        """
        return all(
            (
                self.RAZORPAY_KEY_ID,
                self.RAZORPAY_KEY_SECRET,
                self.RAZORPAY_ACCOUNT_NUMBER,
            )
        )

    @property
    def webhook_verification_configured(self) -> bool:
        return bool(self.RAZORPAY_WEBHOOK_SECRET)

    @property
    def llm_configured(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def all_secrets(self) -> tuple[str, ...]:
        """Every configured secret value, for leak assertions. Never logged."""
        return tuple(
            value
            for value in (
                self.OPENAI_API_KEY,
                self.RAZORPAY_KEY_SECRET,
                self.RAZORPAY_WEBHOOK_SECRET,
                self.RAZORPAY_ACCOUNT_NUMBER,
            )
            if value
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (one parse of the environment per process)."""
    return Settings()


settings = get_settings()
