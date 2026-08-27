"""Scheduled inventory sweep.

Run from the `backend/` directory:

    python -m app.jobs.inventory_check

Intended for cron / a task scheduler. It calls exactly the same service function
as `POST /api/inventory/check`, so the scheduled path and the API path can never
drift apart.

The job deliberately stops at detection. It does **not** create proposals and
certainly does not approve or pay anything: an unattended process must never be
able to spend money, and proposal creation costs LLM calls that a merchant
should choose to spend.
"""

from __future__ import annotations

import logging
import sys

from app.core.database import SessionLocal
from app.services import inventory_service

logger = logging.getLogger("restock.jobs.inventory_check")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s"
    )

    with SessionLocal() as db:
        result = inventory_service.run_inventory_check(db)

        print(
            f"Checked {result.products_checked} products at "
            f"{result.checked_at.isoformat()}"
        )
        if not result.low_stock_products:
            print("No products below their reorder threshold.")
            return 0

        print(f"\n{len(result.low_stock_products)} product(s) below threshold:")
        for product in result.low_stock_products:
            marker = "NEW" if product.id in result.newly_detected_product_ids else "   "
            shortfall = product.reorder_threshold - product.current_stock
            print(
                f"  {marker} {product.name:<16} "
                f"{product.current_stock:>5} / {product.reorder_threshold:<5} "
                f"{product.unit:<8} short by {shortfall}"
            )

        print(
            "\nNo proposals were created. Reordering requires an explicit call to "
            "POST /api/proposals/product/{product_id}."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
