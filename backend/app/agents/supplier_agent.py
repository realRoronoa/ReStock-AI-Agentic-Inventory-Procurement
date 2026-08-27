"""Supplier selection agent.

The agent returns **an id and a reason**. It cannot return a supplier object,
which means it cannot invent a supplier, alter a price, or shorten a delivery
time — the three things a hallucinating model would most plausibly do and the
three that would cost real money.

Everything the model is shown comes from the database, and everything the
backend acts on is re-read from the database afterwards. The model's id is
treated as a *pointer to be checked*, never as data to be trusted.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path
from string import Template
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.agents.llm_client import (
    AgentMalformedOutputError,
    LLMClient,
    get_llm_client,
)
from app.core.money import paise_to_rupees

logger = logging.getLogger("restock.agents.supplier")

PROMPT_PATH = Path(__file__).parent / "prompts" / "supplier_prompt.txt"


# --- agent input ------------------------------------------------------------


class SupplierOption(BaseModel):
    """One supplier, as presented to the model.

    Built from database rows immediately before the call. Suppliers without a
    payable fund account are filtered out upstream rather than shown and then
    rejected, so the model is never asked to choose between options the backend
    would refuse anyway.
    """

    model_config = ConfigDict(frozen=True)

    supplier_id: int
    name: str
    price_per_unit_paise: int
    delivery_days: int

    @property
    def price_per_unit_inr(self) -> Decimal:
        return paise_to_rupees(self.price_per_unit_paise)

    def line_total_inr(self, quantity: int) -> Decimal:
        """Total for this option — computed here, not by the model.

        Shown so the model can reason about magnitude, but the number the
        backend acts on is always recomputed from the supplier row.
        """
        return paise_to_rupees(quantity * self.price_per_unit_paise)


class SupplierSelectionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_name: str
    unit: str
    quantity: int
    current_stock: int
    reorder_threshold: int
    suppliers: tuple[SupplierOption, ...] = Field(min_length=1)


# --- agent output -----------------------------------------------------------


class SupplierSelection(BaseModel):
    """The only thing the supplier agent may return."""

    model_config = ConfigDict(strict=True, frozen=True)

    supplier_id: int = Field(
        description=(
            "Must be one of the offered ids. Verified against the database by "
            "the backend, which also re-checks that it belongs to this product."
        )
    )
    reasoning: str = Field(
        min_length=1,
        description="Why this supplier. Stored verbatim and shown to the merchant.",
    )


@runtime_checkable
class SupplierProvider(Protocol):
    """What `supplier_service` depends on."""

    name: str

    def select(self, request: SupplierSelectionRequest) -> SupplierSelection:
        ...


# --- production implementation ----------------------------------------------


def _load_prompt() -> Template:
    return Template(PROMPT_PATH.read_text(encoding="utf-8"))


def render_options(request: SupplierSelectionRequest) -> str:
    """One line per supplier, with the arithmetic already done."""
    return "\n".join(
        f"- supplier_id={option.supplier_id} | {option.name} | "
        f"INR {option.price_per_unit_inr}/{request.unit} | "
        f"delivery {option.delivery_days} days | "
        f"total for {request.quantity} {request.unit}: "
        f"INR {option.line_total_inr(request.quantity)}"
        for option in request.suppliers
    )


class LLMSupplierProvider:
    """Asks a configured LLM which supplier to use."""

    name = "llm-supplier"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()
        self._template = _load_prompt()

    def select(self, request: SupplierSelectionRequest) -> SupplierSelection:
        user_prompt = self._template.substitute(
            product_name=request.product_name,
            unit=request.unit,
            quantity=request.quantity,
            current_stock=request.current_stock,
            reorder_threshold=request.reorder_threshold,
            supplier_options=render_options(request),
            allowed_ids=", ".join(
                str(option.supplier_id) for option in request.suppliers
            ),
        )

        raw = self._client.complete_json(
            system=(
                "You are a procurement assistant. You choose between supplier "
                "options that are given to you, and you reply with a single "
                "JSON object and nothing else."
            ),
            user=user_prompt,
        )

        logger.info(
            "supplier_raw product=%s keys=%s", request.product_name, sorted(raw)
        )

        try:
            return SupplierSelection.model_validate(raw)
        except Exception as exc:
            raise AgentMalformedOutputError(
                f"Supplier selection did not match the required schema: "
                f"{json.dumps(raw)[:200]}"
            ) from exc
