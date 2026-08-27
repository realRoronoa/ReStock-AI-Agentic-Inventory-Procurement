"""Demand forecasting agent.

The agent's entire output surface is `ForecastResult`: a quantity and the
reasoning behind it. It has no other effect on the world.

Validation strategy — `ForecastResult` is declared **strict**, so
`{"recommended_quantity": "75"}` is rejected rather than coerced. That is a
deliberate choice about untrusted input: a model that returns the wrong *type*
has misunderstood the contract, and silently accepting a string here would mean
accepting whatever else it decided to reinterpret. The failure is cheap (one
rejected proposal, clearly audited) and the alternative is a system that quietly
tolerates drift in a value that becomes a bank transfer.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from string import Template
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.agents.llm_client import (
    AgentMalformedOutputError,
    LLMClient,
    get_llm_client,
)

logger = logging.getLogger("restock.agents.forecast")

PROMPT_PATH = Path(__file__).parent / "prompts" / "forecast_prompt.txt"


# --- agent input ------------------------------------------------------------


class SalesObservation(BaseModel):
    """One observed day of demand."""

    model_config = ConfigDict(frozen=True)

    date: date
    quantity_sold: int


class ForecastRequest(BaseModel):
    """Everything the model is told.

    Note the absences: no prices, no supplier information, no spend limits. The
    forecast question is "how much will sell", and giving the model the budget
    ceiling would let it tailor a quantity to fit under a cap rather than to
    match demand — which would corrupt the very number the guardrails exist to
    check.
    """

    model_config = ConfigDict(frozen=True)

    product_name: str
    unit: str
    current_stock: int
    reorder_threshold: int
    sales_history: tuple[SalesObservation, ...]
    lead_time_days: int = Field(
        description="Longest supplier delivery time, so cover can be reasoned about."
    )

    @property
    def history_days(self) -> int:
        return len(self.sales_history)


# --- agent output -----------------------------------------------------------


class ForecastResult(BaseModel):
    """The only thing the forecast agent may return.

    `strict=True` means no type coercion: a quantity must arrive as a JSON
    integer.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    recommended_quantity: int = Field(
        description="Units to reorder. Bounds are checked by the backend, not here."
    )
    reasoning: str = Field(
        min_length=1,
        description="Why this quantity. Shown to the merchant and stored verbatim.",
    )


@runtime_checkable
class ForecastProvider(Protocol):
    """What `forecast_service` depends on."""

    name: str

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Return a forecast, or raise `AgentTransportError` /
        `AgentMalformedOutputError`."""
        ...


# --- production implementation ----------------------------------------------


def _load_prompt() -> Template:
    """Load the prompt template.

    `string.Template` with `$name` placeholders rather than `str.format`,
    because the prompt contains literal JSON braces that `format` would try to
    interpret.
    """
    return Template(PROMPT_PATH.read_text(encoding="utf-8"))


def render_history(observations: tuple[SalesObservation, ...]) -> str:
    """Compact, oldest-first table of daily sales.

    Given as a table rather than prose so the model is not tempted to trust a
    summary statistic this code computed for it. It sees the raw observations
    and does its own reading of the trend.
    """
    lines = [
        f"{observation.date.isoformat()} ({observation.date.strftime('%a')}): "
        f"{observation.quantity_sold}"
        for observation in observations
    ]
    return "\n".join(lines)


class LLMForecastProvider:
    """Asks a configured LLM for a demand forecast."""

    name = "llm-forecast"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()
        self._template = _load_prompt()

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        user_prompt = self._template.substitute(
            product_name=request.product_name,
            unit=request.unit,
            current_stock=request.current_stock,
            reorder_threshold=request.reorder_threshold,
            history_days=request.history_days,
            lead_time_days=request.lead_time_days,
            sales_history=render_history(request.sales_history),
        )

        raw = self._client.complete_json(
            system=(
                "You are a demand-forecasting assistant for a retail inventory "
                "system. You reply with a single JSON object and nothing else."
            ),
            user=user_prompt,
        )

        logger.info(
            "forecast_raw product=%s keys=%s", request.product_name, sorted(raw)
        )

        try:
            # Validated here so a schema violation is reported as bad model
            # output rather than surfacing as an unrelated TypeError later.
            return ForecastResult.model_validate(raw)
        except Exception as exc:
            raise AgentMalformedOutputError(
                f"Forecast did not match the required schema: "
                f"{json.dumps(raw)[:200]}"
            ) from exc
