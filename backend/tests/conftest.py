"""Shared pytest fixtures.

Test isolation rules enforced here:

1. **No developer `.env` leakage.** Environment variables are set *before* any
   `app` module is imported. pydantic-settings ranks real environment variables
   above `.env`, so a local file holding real RazorpayX or OpenAI credentials
   cannot influence a test run.
2. **No real external calls.** Credentials are blanked, so any code path that
   would reach RazorpayX or an LLM must fail loudly rather than silently
   contacting a live service.
3. **Fresh schema per test.** Every test gets an empty in-memory database.
"""

from __future__ import annotations

import os

# --- Must run before importing anything from `app` --------------------------
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
# No LLM_PROVIDER override: fakes are injected as objects, not selected by
# configuration, so production code has no fake-provider code path at all.
os.environ["OPENAI_API_KEY"] = ""
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""
os.environ["RAZORPAY_ACCOUNT_NUMBER"] = ""
os.environ["RAZORPAY_WEBHOOK_SECRET"] = ""
os.environ["MAX_ORDER_SPEND_INR"] = "10000"
os.environ["MAX_DAILY_SPEND_INR"] = "25000"
os.environ["MAX_REORDER_QUANTITY"] = "500"

from datetime import date  # noqa: E402
from typing import Generator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.config import settings as app_settings  # noqa: E402
from app.core.database import Base, build_engine, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.supplier import Supplier  # noqa: E402

# Import for metadata registration side effect.
import app.models  # noqa: F401,E402


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    """One in-memory SQLite engine for the whole session.

    StaticPool keeps every session on the same connection, which is what makes
    an in-memory database visible across the test session and the app.
    """
    eng = build_engine("sqlite+pysqlite:///:memory:")
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _fresh_schema(engine: Engine) -> Generator[None, None, None]:
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
) -> Generator[TestClient, None, None]:
    """TestClient wired to the in-memory database."""

    def override_get_db() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


# --- Domain helpers ---------------------------------------------------------


@pytest.fixture
def make_product(db: Session):
    """Factory for a product with optional suppliers.

    Prefer this over the demo seed in unit tests: each test states exactly the
    numbers its assertions depend on.
    """

    def _make(
        name: str = "Widget",
        *,
        current_stock: int = 10,
        reorder_threshold: int = 50,
        unit: str = "unit",
        suppliers: list[dict] | None = None,
    ) -> Product:
        product = Product(
            name=name,
            unit=unit,
            current_stock=current_stock,
            reorder_threshold=reorder_threshold,
        )
        db.add(product)
        db.flush()

        for index, spec in enumerate(suppliers or []):
            db.add(
                Supplier(
                    product_id=product.id,
                    name=spec.get("name", f"{name} Supplier {index + 1}"),
                    price_per_unit_paise=spec.get("price_per_unit_paise", 10_000),
                    delivery_days=spec.get("delivery_days", 3),
                    razorpay_fund_account_id=spec.get(
                        "razorpay_fund_account_id", f"fa_TEST{name[:4].upper()}{index}"
                    ),
                )
            )

        db.commit()
        db.refresh(product)
        return product

    return _make


@pytest.fixture
def seeded_db(db: Session) -> Session:
    """The full demo dataset, for tests that need realistic history."""
    from app.seed.seed import seed

    seed(db, today=date(2025, 6, 1))
    return db


# --- External-service doubles ----------------------------------------------


@pytest.fixture
def webhook_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Install a known webhook secret for the duration of a test.

    Blank by default (see the environment setup at the top of this file), which
    means webhook verification fails closed unless a test opts in. That is the
    production-safe default and it also makes the "unverifiable" test case real.
    """
    from tests.fakes import TEST_WEBHOOK_SECRET

    monkeypatch.setattr(
        app_settings, "RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET, raising=True
    )
    return TEST_WEBHOOK_SECRET


@pytest.fixture
def razorpay_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend RazorpayX credentials are present.

    Only ever used with a fake provider or a mocked transport. No test in this
    suite can reach the real RazorpayX API: the values are obvious dummies and
    the base URL is never contacted without a MockTransport.
    """
    monkeypatch.setattr(app_settings, "RAZORPAY_KEY_ID", "rzp_test_dummy", raising=True)
    monkeypatch.setattr(
        app_settings, "RAZORPAY_KEY_SECRET", "dummy_secret_value", raising=True
    )
    monkeypatch.setattr(
        app_settings, "RAZORPAY_ACCOUNT_NUMBER", "2323230099089860", raising=True
    )


@pytest.fixture
def providers():
    """Bundle of default fakes, mutable per test."""
    from tests.fakes import FakeForecastProvider, FakePaymentProvider, FakeSupplierProvider

    class Bundle:
        def __init__(self) -> None:
            self.forecast = FakeForecastProvider()
            self.supplier = FakeSupplierProvider()
            self.payment = FakePaymentProvider()

    return Bundle()


@pytest.fixture
def wired_client(session_factory, providers):
    """TestClient with every external dependency replaced by a double.

    Overrides go through `app.dependency_overrides`, which is why the production
    code has provider *factories* as FastAPI dependencies: nothing has to know
    that a fake exists.
    """
    from app.services.forecast_service import get_forecast_provider
    from app.services.payment_service import get_payment_provider
    from app.services.supplier_service import get_supplier_provider

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_forecast_provider] = lambda: providers.forecast
    fastapi_app.dependency_overrides[get_supplier_provider] = lambda: providers.supplier
    fastapi_app.dependency_overrides[get_payment_provider] = lambda: providers.payment

    with TestClient(fastapi_app) as test_client:
        yield test_client

    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def low_stock_product(db: Session, make_product):
    """Milk at 42/60 with two suppliers presenting a real trade-off.

    Priced so that a 75-unit order comes to INR 3,600 — comfortably inside the
    INR 10,000 per-order cap, so guardrail tests can breach it deliberately
    rather than by accident.
    """
    from datetime import date, timedelta

    from app.models.sales_history import SalesHistory

    product = make_product(
        "Milk",
        current_stock=42,
        reorder_threshold=60,
        unit="litre",
        suppliers=[
            {
                "name": "Amul Dairy Direct",
                "price_per_unit_paise": 4_800,
                "delivery_days": 2,
                "razorpay_fund_account_id": "fa_TESTMILKAMUL01",
            },
            {
                "name": "Krishna Dairy Co-op",
                "price_per_unit_paise": 4_400,
                "delivery_days": 6,
                "razorpay_fund_account_id": "fa_TESTMILKKRSH01",
            },
        ],
    )
    start = date(2025, 6, 1)
    for offset in range(28):
        db.add(
            SalesHistory(
                product_id=product.id,
                date=start + timedelta(days=offset),
                quantity_sold=20,
            )
        )
    db.commit()
    db.refresh(product)
    return product
