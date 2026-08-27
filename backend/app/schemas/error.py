"""The API error envelope, as an explicit schema.

Declared so it appears in the OpenAPI document and the frontend can generate a
type for it, rather than discovering the shape by trial and error.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(
        description="Stable machine-readable code. Branch on this, not the message.",
        examples=["ORDER_NOT_PROPOSED"],
    )
    message: str = Field(
        description="Human-readable explanation, safe to display.",
        examples=["Only proposed orders can be approved."],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context, e.g. the limit that was breached.",
    )


class ErrorResponse(BaseModel):
    error: ErrorDetail


#: Reusable OpenAPI `responses` fragments so every route documents its failures
#: without repeating the schema.
def error_responses(*codes_and_descriptions: tuple[int, str]) -> dict[int, dict]:
    return {
        status: {"model": ErrorResponse, "description": description}
        for status, description in codes_and_descriptions
    }
