"""Merchant-editable settings.

A **singleton row** (`id = 1`), matching the single-shared-workspace decision.

This is where the spend guardrails move from environment-only to editable. The
environment variables (`MAX_ORDER_SPEND_INR` and friends) remain the bootstrap
defaults: if no row exists, or a column is NULL, the environment value applies.
That keeps every existing test — which knows nothing about this table — working
unchanged, and means a fresh deployment behaves exactly as before until someone
deliberately changes a limit.

Editing a limit requires an authenticated user and is written to the audit trail
with the old and new values. An unauthenticated client cannot raise its own
spending cap; that was the objection to making these writable at all, and auth
is what answers it.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.money import paise_to_rupees

#: The only row this table ever has.
SINGLETON_ID = 1


class MerchantSettings(Base, TimestampMixin):
    __tablename__ = "merchant_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- spend guardrails (NULL = fall back to the environment) -------------
    max_order_spend_paise: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    max_daily_spend_paise: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    max_reorder_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- automation preferences --------------------------------------------
    #: Whether a scheduled sweep should run. Read by the cron entry point;
    #: there is no in-process scheduler, so this is a flag for the operator.
    auto_check_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    notify_on_proposal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    #: Never editable, and stored only so the UI can render it as fixed-on.
    #: Approval is structurally mandatory: there is no code path that pays
    #: without `POST /api/orders/{id}/approve`, so this cannot be turned off.
    require_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # --- workspace ---------------------------------------------------------
    business_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Chosen during onboarding: shopify | square | csv | sample. Recorded
    #: faithfully, but selecting one syncs nothing — no integration exists.
    connected_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="singleton_row"),
        CheckConstraint(
            "max_order_spend_paise IS NULL OR max_order_spend_paise > 0",
            name="max_order_spend_positive",
        ),
        CheckConstraint(
            "max_daily_spend_paise IS NULL OR max_daily_spend_paise > 0",
            name="max_daily_spend_positive",
        ),
        CheckConstraint(
            "max_reorder_quantity IS NULL OR max_reorder_quantity > 0",
            name="max_reorder_quantity_positive",
        ),
    )

    @property
    def max_order_spend(self) -> Decimal | None:
        if self.max_order_spend_paise is None:
            return None
        return paise_to_rupees(self.max_order_spend_paise)

    @property
    def max_daily_spend(self) -> Decimal | None:
        if self.max_daily_spend_paise is None:
            return None
        return paise_to_rupees(self.max_daily_spend_paise)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MerchantSettings order={self.max_order_spend_paise} "
            f"daily={self.max_daily_spend_paise} qty={self.max_reorder_quantity}>"
        )
