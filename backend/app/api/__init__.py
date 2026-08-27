"""HTTP layer.

Routers translate HTTP <-> domain calls and nothing else: no business rules, no
external API calls, no direct state transitions. Everything of consequence
lives in `app.services`.

Sub-routers are registered here as milestones land, so `main.py` mounts exactly
one router.
"""

from fastapi import APIRouter

from app.api import (
    audit,
    inventory,
    orders,
    products,
    proposals,
    sales,
    settings,
    spending,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(products.router)
api_router.include_router(inventory.router)
api_router.include_router(sales.router)
api_router.include_router(proposals.router)
api_router.include_router(orders.router)
api_router.include_router(audit.router)
api_router.include_router(spending.router)
api_router.include_router(settings.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
