"""LLM agents.

Architectural boundary — this package is where model output is produced, and
nowhere else. Two rules hold, and both are enforced by a test rather than by
convention (`tests/test_architecture.py`):

1. **No side effects.** Nothing here may import `app.services` or
   `app.models`. An agent cannot write to the database, change an order state,
   or touch inventory.
2. **No payment access.** Nothing here may import `payment_service`, `httpx`
   toward RazorpayX, or any payment credential. An agent cannot create a payout
   and cannot see a Razorpay secret.

What agents return is a validated Pydantic object. Services then re-validate it
against the database before acting. The model recommends; it never decides.
"""

from app.agents.forecast_agent import (
    ForecastProvider,
    ForecastRequest,
    ForecastResult,
    LLMForecastProvider,
    SalesObservation,
)
from app.agents.llm_client import (
    AgentMalformedOutputError,
    AgentNotConfigured,
    AgentTransportError,
    LLMClient,
    OpenAIJSONClient,
)
from app.agents.supplier_agent import (
    LLMSupplierProvider,
    SupplierOption,
    SupplierProvider,
    SupplierSelection,
    SupplierSelectionRequest,
)

__all__ = [
    "AgentMalformedOutputError",
    "AgentNotConfigured",
    "AgentTransportError",
    "ForecastProvider",
    "ForecastRequest",
    "ForecastResult",
    "LLMClient",
    "LLMForecastProvider",
    "LLMSupplierProvider",
    "OpenAIJSONClient",
    "SalesObservation",
    "SupplierOption",
    "SupplierProvider",
    "SupplierSelection",
    "SupplierSelectionRequest",
]
