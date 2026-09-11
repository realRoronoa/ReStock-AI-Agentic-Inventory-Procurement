"""The single seam between this application and an LLM vendor.

Every model call in the system goes through `complete_json`. That is what makes
"the provider is swappable" true rather than aspirational: `OPENAI_BASE_URL`
points at anything speaking the OpenAI chat-completions shape, and replacing the
vendor entirely means adding one class here and nothing else.

What this module deliberately does **not** do:

* It does not import anything from `app.services` or `app.models`. Nothing
  reachable from here can write to the database or move money.
* It does not know what a forecast or a supplier is. It sends text and returns
  parsed JSON; meaning is the agents' business, validation is the services'.
* It never receives a payment credential. Prompts are asserted secret-free
  before they leave the process.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.config import Settings, settings as default_settings
from app.core.security import assert_no_secrets, mask_secret

logger = logging.getLogger("restock.llm")


class AgentTransportError(RuntimeError):
    """The model could not be reached, or refused to answer.

    Kept separate from `app.core.errors` so the agent layer carries no HTTP
    status codes and no API-shaped concepts. Services translate these into
    client-facing errors.
    """


class AgentMalformedOutputError(RuntimeError):
    """The model answered, but not with usable JSON."""


class AgentNotConfigured(RuntimeError):
    """No API key is present, so no model call is possible."""


@runtime_checkable
class LLMClient(Protocol):
    """Minimal contract: text in, JSON object out."""

    model: str

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        ...


class OpenAIJSONClient:
    """Chat-completions client constrained to JSON output.

    Uses `response_format={"type": "json_object"}` so the vendor enforces
    syntactic JSON server-side. That removes the most common failure mode
    (prose wrapped around the answer, or a markdown fence) but is **not**
    treated as a guarantee: the response is still parsed defensively here and
    still schema-validated by the caller, because a syntactically valid JSON
    object can carry semantically absurd values.
    """

    def __init__(
        self,
        config: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config or default_settings
        self.model = self._config.OPENAI_MODEL
        # Injected only by tests, to mount a MockTransport.
        self._client = client

    def _ensure_configured(self) -> None:
        if not self._config.OPENAI_API_KEY:
            raise AgentNotConfigured(
                "OPENAI_API_KEY is not set, so no recommendation can be generated."
            )

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self._ensure_configured()

        # Belt-and-braces: a prompt is assembled from several sources, and a
        # credential reaching a third party would be unrecoverable.
        assert_no_secrets(system, self._config.all_secrets)
        assert_no_secrets(user, self._config.all_secrets)

        url = f"{self._config.OPENAI_BASE_URL.rstrip('/')}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            # Low but non-zero: the task is analytical, so determinism is worth
            # more than variety, but nothing here depends on exact repeatability.
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self._config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        logger.info(
            "llm_request model=%s key=%s system_chars=%d user_chars=%d",
            self.model,
            mask_secret(self._config.OPENAI_API_KEY, keep=7),
            len(system),
            len(user),
        )

        try:
            response = self._post(url, body, headers)
        except httpx.TimeoutException as exc:
            # No retry. A retried LLM call costs money and latency, and a
            # forecast is never urgent enough to justify hammering a provider
            # that is already struggling. The merchant can ask again.
            raise AgentTransportError(
                f"Model did not respond within "
                f"{self._config.LLM_TIMEOUT_SECONDS:.0f}s."
            ) from exc
        except httpx.RequestError as exc:
            raise AgentTransportError(
                f"Could not reach the model provider ({type(exc).__name__})."
            ) from exc

        if response.status_code >= 400:
            # The provider's error body can echo the prompt; log the status only.
            logger.error("llm_error http_status=%s", response.status_code)
            raise AgentTransportError(
                f"Model provider returned HTTP {response.status_code}."
            )

        return self._parse(response)

    def _post(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        timeout = self._config.LLM_TIMEOUT_SECONDS
        if self._client is not None:
            return self._client.post(url, json=body, headers=headers, timeout=timeout)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=body, headers=headers)

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentMalformedOutputError(
                "Model response did not contain a message."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise AgentMalformedOutputError("Model returned an empty message.")

        try:
            parsed = json.loads(content)
        except ValueError as exc:
            # Truncated so a long hallucination cannot flood the log, and
            # recorded because "what did it actually say" is the first question
            # when a forecast starts failing.
            logger.warning("llm_malformed_json content_prefix=%r", content[:200])
            raise AgentMalformedOutputError(
                "Model returned text that is not valid JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise AgentMalformedOutputError(
                f"Model returned JSON of type {type(parsed).__name__}, expected "
                "an object."
            )

        logger.info("llm_response model=%s keys=%s", self.model, sorted(parsed))
        return parsed


class GeminiJSONClient:
    """Gemini API client constrained to JSON output.

    Uses `generationConfig.responseMimeType = "application/json"` with
    the Generative Language REST API (`generateContent`).
    """

    def __init__(
        self,
        config: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._config = config or default_settings
        self.model = self._config.GEMINI_MODEL
        # Injected only by tests, to mount a MockTransport.
        self._client = client

    def _ensure_configured(self) -> None:
        if not self._config.GEMINI_API_KEY:
            raise AgentNotConfigured(
                "GEMINI_API_KEY is not set, so no recommendation can be generated."
            )

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        self._ensure_configured()

        assert_no_secrets(system, self._config.all_secrets)
        assert_no_secrets(user, self._config.all_secrets)

        url = f"{self._config.GEMINI_BASE_URL.rstrip('/')}/models/{self.model}:generateContent"
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system}],
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        }
        headers = {
            "x-goog-api-key": self._config.GEMINI_API_KEY,
            "Content-Type": "application/json",
        }

        logger.info(
            "llm_request provider=gemini model=%s key=%s system_chars=%d user_chars=%d",
            self.model,
            mask_secret(self._config.GEMINI_API_KEY, keep=7),
            len(system),
            len(user),
        )

        try:
            response = self._post(url, body, headers)
        except httpx.TimeoutException as exc:
            raise AgentTransportError(
                f"Model did not respond within {self._config.LLM_TIMEOUT_SECONDS:.0f}s."
            ) from exc
        except httpx.RequestError as exc:
            raise AgentTransportError(
                f"Could not reach the model provider ({type(exc).__name__})."
            ) from exc

        if response.status_code >= 400:
            logger.error(
                "llm_error provider=gemini http_status=%s", response.status_code
            )
            raise AgentTransportError(
                f"Model provider returned HTTP {response.status_code}."
            )

        return self._parse(response)

    def _post(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        timeout = self._config.LLM_TIMEOUT_SECONDS
        if self._client is not None:
            return self._client.post(url, json=body, headers=headers, timeout=timeout)
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=body, headers=headers)

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        try:
            envelope = response.json()
            candidates = envelope.get("candidates")
            if not candidates or not isinstance(candidates, list):
                raise AgentMalformedOutputError(
                    "Model response did not contain candidates."
                )
            content = candidates[0]["content"]["parts"][0]["text"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            if isinstance(exc, AgentMalformedOutputError):
                raise
            raise AgentMalformedOutputError(
                "Model response did not contain a message."
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise AgentMalformedOutputError("Model returned an empty message.")

        clean_content = content.strip()
        if clean_content.startswith("```"):
            lines = clean_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_content = "\n".join(lines).strip()

        try:
            parsed = json.loads(clean_content)
        except ValueError as exc:
            logger.warning("llm_malformed_json content_prefix=%r", content[:200])
            raise AgentMalformedOutputError(
                "Model returned text that is not valid JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise AgentMalformedOutputError(
                f"Model returned JSON of type {type(parsed).__name__}, expected an object."
            )

        logger.info(
            "llm_response provider=gemini model=%s keys=%s",
            self.model,
            sorted(parsed),
        )
        return parsed


def get_llm_client(config: Settings | None = None) -> LLMClient:
    """The configured client based on LLM_PROVIDER."""
    cfg = config or default_settings
    if cfg.LLM_PROVIDER == "gemini":
        return GeminiJSONClient(cfg)
    return OpenAIJSONClient(cfg)

