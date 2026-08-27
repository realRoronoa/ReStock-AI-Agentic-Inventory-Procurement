"""Users and sessions.

**Single shared workspace.** A user account is an identity, not a tenant: every
authenticated user sees the same inventory. True multi-tenancy would mean a
`workspace_id` on products, suppliers, sales, orders and audit, which is a
different piece of work. Documented in `docs/frontend-map.md`.

Sessions are **opaque server-side tokens**, not JWTs. A JWT cannot be revoked
before it expires, and this application authorises payouts — logging out, or
noticing a stolen token, has to actually end the session. The cost is one table.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, UTCDateTime, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.billing import Invoice, PaymentMethod, Subscription


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Stored lower-cased so lookup is case-insensitive without a functional
    #: index (which SQLite and PostgreSQL spell differently).
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    #: Argon2id hash. The plaintext password never leaves the request handler.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)

    #: The seeded demo account. Flagged so the UI can label it and so it can be
    #: excluded from anything that should only apply to real accounts.
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    invoices: Mapped[list["Invoice"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    payment_methods: Mapped[list["PaymentMethod"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def initials(self) -> str:
        """Two-letter avatar text, as the design's `.avatar` element shows."""
        parts = [part for part in self.full_name.split() if part]
        if not parts:
            return self.email[:2].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r} demo={self.is_demo}>"


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: SHA-256 of the token, never the token itself. A database dump therefore
    #: does not hand over live sessions — the same reason passwords are hashed.
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)

    #: Set on logout. Kept rather than deleted so "when did this session end?"
    #: stays answerable.
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="sessions")

    def is_active(self, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.revoked_at is None and self.expires_at > now

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Session id={self.id} user_id={self.user_id} active={self.is_active()}>"
