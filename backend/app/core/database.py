"""Database engine, session factory, and declarative base.

Design notes
------------
* **Explicit constraint naming.** A naming convention is attached to the
  metadata so that Alembic autogenerate produces stable, named constraints.
  Without it, SQLite constraints are anonymous and cannot be altered or
  dropped in a later migration.
* **SQLite foreign keys.** SQLite ignores foreign keys unless
  `PRAGMA foreign_keys=ON` is issued *per connection*. Spec section 7 requires
  real referential integrity, so a connect-time event listener enables it.
  Without this, the FK constraints in the models would be decorative.
* **PostgreSQL compatibility.** No SQLite-only types are used. JSON columns
  declare a JSONB variant so Postgres gets the indexable type automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import JSON, DateTime, MetaData, TypeDecorator, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

# --- Portable column types --------------------------------------------------

#: JSON on SQLite, JSONB on PostgreSQL. Use for audit metadata / raw payloads.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Current UTC time as an aware datetime.

    Used as a Python-side default rather than `func.now()` because SQLite's
    `CURRENT_TIMESTAMP` is naive local-ish text, which would make timestamps
    behave differently across SQLite and PostgreSQL.
    """
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """A timestamp that is genuinely timezone-aware on every backend.

    `DateTime(timezone=True)` alone is not enough: SQLite has no timestamp type,
    so it silently discards `tzinfo` on write and returns a *naive* datetime on
    read. An API response would then serialise `2026-08-27T08:58:17` with no
    offset, and every client would read it as local time — a real, silent bug
    that only appears on SQLite and would vanish on PostgreSQL.

    This decorator normalises both directions:

    * on write, an aware value is converted to UTC; a naive value is assumed to
      already be UTC (rejecting it would break Alembic-generated defaults);
    * on read, a naive value is stamped with UTC.

    PostgreSQL already behaves this way, so the decorator is a no-op there and
    the underlying column type stays `TIMESTAMP WITH TIME ZONE`.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


# --- Declarative base -------------------------------------------------------

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """`created_at` / `updated_at` columns with portable UTC defaults."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


# --- Engine -----------------------------------------------------------------


def build_engine(database_url: str, **kwargs: Any) -> Engine:
    """Create an engine with the right options for the target dialect."""
    options: dict[str, Any] = {"future": True, "pool_pre_ping": True}

    if database_url.startswith("sqlite"):
        # FastAPI serves requests on a threadpool; SQLite connections would
        # otherwise refuse cross-thread use.
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            # An in-memory database lives inside a single connection, so all
            # sessions must share one. Used by the test suite.
            options["poolclass"] = StaticPool
            options.pop("pool_pre_ping", None)

    options.update(kwargs)
    return create_engine(database_url, **options)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Turn on SQLite FK enforcement for every new connection."""
    # Identified by duck-typing rather than an import so the listener stays
    # harmless when running against PostgreSQL.
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


engine = build_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session.

    Services own their transactions (commit/rollback); this dependency only
    guarantees the session is closed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
