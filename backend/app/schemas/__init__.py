"""Pydantic request/response schemas.

Kept strictly separate from `app.models` (SQLAlchemy persistence) so the wire
contract can evolve independently of the database, and so no client-supplied
payload can ever be written straight to a table.
"""

from app.schemas.product import ProductDetail, ProductRead, SalesPoint, SupplierRead

__all__ = ["ProductDetail", "ProductRead", "SalesPoint", "SupplierRead"]
