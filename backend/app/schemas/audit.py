"""Audit log schemas.

Read-only by design. There is no create, update, or delete schema, because the
API exposes no way to write or alter audit history — the trail is append-only
from the application's perspective and entirely so from a client's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_log import AuditActor


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    actor: AuditActor = Field(
        description=(
            "agent = an LLM produced this; human = a person acted; "
            "system = deterministic backend code."
        )
    )
    action: str = Field(description="Canonical event name, e.g. PAYOUT_PROCESSED.")
    reasoning_text: str | None = Field(
        default=None,
        description=(
            "For agent events this is the model's own reasoning, verbatim. For "
            "system events, a description of what happened and why."
        ),
    )
    related_order_id: int | None = None
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias="event_metadata",
        serialization_alias="metadata",
        description="Structured context. Scrubbed of anything secret-shaped.",
    )


class AuditTrailResponse(BaseModel):
    """The decision chain for one order, oldest first."""

    order_id: int
    entries: list[AuditLogRead]
    entry_count: int
