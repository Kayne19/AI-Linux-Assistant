from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    Anthropic = None


@dataclass(frozen=True)
class AnthropicStructuredOutputClientConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None


class AnthropicStructuredOutputClient:
    def __init__(self, config: AnthropicStructuredOutputClientConfig) -> None:
        if Anthropic is None:
            raise RuntimeError("Anthropic SDK is not installed. Add the 'anthropic' package to use Anthropic clients.")
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
        }
        if config.request_timeout_seconds is not None:
            client_kwargs["timeout"] = config.request_timeout_seconds
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.config = config
        self.client = Anthropic(**client_kwargs)

    def request_json(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict[str, Any],
        schema_description: str = "",
        previous_response_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        del previous_response_id, tools, reasoning_effort
        request_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "system": instructions,
            "messages": [{"role": "user", "content": user_input}],
            "max_tokens": self.config.max_output_tokens if max_output_tokens is None else max_output_tokens or 4096,
            "tools": [
                {
                    "name": schema_name,
                    "description": schema_description,
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": schema_name},
        }
        if self.config.temperature is not None:
            request_kwargs["temperature"] = self.config.temperature
        response = self.client.messages.create(**request_kwargs)
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == schema_name:
                payload = getattr(block, "input", None)
                if isinstance(payload, dict):
                    return payload
                raise RuntimeError(f"Anthropic returned non-object structured output for {schema_name!r}.")
        raise RuntimeError(f"Anthropic returned no structured tool payload for {schema_name!r}.")
