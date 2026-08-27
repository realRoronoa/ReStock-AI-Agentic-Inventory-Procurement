"""SQLAlchemy models.

Importing this package registers every mapped class on `Base.metadata`, which
is what Alembic autogenerate and `create_all` rely on. Import order matters
only in that all models must be imported before mappers are configured.
"""

from app.models.audit_log import AuditAction, AuditActor, AuditLog
from app.models.billing import (
    BillingCycle,
    Invoice,
    InvoiceStatus,
    PaymentMethod,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.models.merchant_settings import SINGLETON_ID, MerchantSettings
from app.models.order import ALLOWED_TRANSITIONS, Order, OrderStatus
from app.models.product import Product
from app.models.sales_history import SalesHistory
from app.models.supplier import Supplier
from app.models.user import Session, User
from app.models.webhook_event import WebhookEvent, WebhookOutcome

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AuditAction",
    "AuditActor",
    "AuditLog",
    "BillingCycle",
    "Invoice",
    "InvoiceStatus",
    "MerchantSettings",
    "PaymentMethod",
    "Plan",
    "SINGLETON_ID",
    "Session",
    "Subscription",
    "SubscriptionStatus",
    "User",
    "Order",
    "OrderStatus",
    "Product",
    "SalesHistory",
    "Supplier",
    "WebhookEvent",
    "WebhookOutcome",
]
