from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

try:
    from google import genai
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    genai = None


@dataclass(frozen=True)
class GoogleGenAIStructuredOutputClientConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class GoogleGenAIStructuredOutputClient:
    def __init__(self, config: GoogleGenAIStructuredOutputClientConfig) -> None:
        if genai is None:
            raise RuntimeError("Google GenAI SDK is not installed. Add the 'google-genai' package to use Google clients.")
        if config.base_url:
            raise ValueError("Google GenAI client does not support base_url overrides in the eval harness.")
        self.config = config
        self.client = genai.Client(
            api_key=config.api_key,
            http_options={"timeout": config.request_timeout_seconds},
        )

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
        del schema_name, schema_description, previous_response_id, tools, reasoning_effort
        config: dict[str, Any] = {
            "system_instruction": instructions,
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        resolved_max_output_tokens = self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        if resolved_max_output_tokens is not None:
            config["max_output_tokens"] = resolved_max_output_tokens
        response = self.client.models.generate_content(
            model=self.config.model,
            contents=user_input,
            config=config,
        )
        payload_text = str(getattr(response, "text", "") or "").strip()
        if not payload_text:
            raise RuntimeError("Google GenAI returned no structured output.")
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise RuntimeError(f"Google GenAI returned non-object JSON: {type(payload).__name__!r}")
        return payload
