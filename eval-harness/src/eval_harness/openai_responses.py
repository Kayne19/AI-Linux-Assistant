from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    OpenAI = None


def _item_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


@dataclass(frozen=True)
class OpenAIResponsesClientConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ResponsesFunctionCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


def extract_function_calls(response: Any) -> tuple[ResponsesFunctionCall, ...]:
    tool_calls: list[ResponsesFunctionCall] = []
    for item in _item_get(response, "output", []) or []:
        if _item_get(item, "type") != "function_call":
            continue
        raw_arguments = _item_get(item, "arguments", {})
        if isinstance(raw_arguments, dict):
            arguments = raw_arguments
        else:
            try:
                arguments = json.loads(str(raw_arguments))
            except json.JSONDecodeError:
                arguments = {"raw_arguments": str(raw_arguments)}
        tool_calls.append(
            ResponsesFunctionCall(
                name=str(_item_get(item, "name", "unknown_tool")),
                arguments=arguments,
                call_id=_item_get(item, "call_id"),
            )
        )
    return tuple(tool_calls)


def function_call_output_item(call_id: str, output: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    normalized_output = output if isinstance(output, str) else json.dumps(output)
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": normalized_output,
    }


class OpenAIResponsesClient:
    def __init__(self, config: OpenAIResponsesClientConfig, *, client: Any | None = None):
        self.config = config
        self.client = client or self._build_client(config)

    def _build_client(self, config: OpenAIResponsesClientConfig) -> Any:
        if OpenAI is None:
            raise RuntimeError("OpenAI SDK is not installed. Add the 'openai' package to use Responses API clients.")
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "timeout": config.request_timeout_seconds,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        return OpenAI(**client_kwargs)

    def create_response(
        self,
        *,
        instructions: str,
        input_items: str | Sequence[dict[str, Any]],
        text_format: dict[str, Any] | None = None,
        previous_response_id: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        request_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": self._normalize_input_items(input_items),
        }
        resolved_max_output_tokens = self.config.max_output_tokens if max_output_tokens is None else max_output_tokens
        if resolved_max_output_tokens is not None:
            request_kwargs["max_output_tokens"] = resolved_max_output_tokens
        resolved_reasoning_effort = (
            self.config.reasoning_effort if reasoning_effort is None else reasoning_effort
        )
        if resolved_reasoning_effort:
            request_kwargs["reasoning"] = {"effort": resolved_reasoning_effort}
        if previous_response_id:
            request_kwargs["previous_response_id"] = previous_response_id
        if tools:
            request_kwargs["tools"] = list(tools)
            request_kwargs["tool_choice"] = "auto"
            request_kwargs["parallel_tool_calls"] = True
        if text_format is not None:
            request_kwargs["text"] = {"format": text_format}
        return self.client.responses.create(**request_kwargs)

    def request_json(
        self,
        *,
        instructions: str,
        user_input: str | Sequence[dict[str, Any]],
        schema_name: str,
        schema: dict[str, Any],
        schema_description: str = "",
        previous_response_id: str | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        response = self.create_response(
            instructions=instructions,
            input_items=user_input,
            previous_response_id=previous_response_id,
            tools=tools,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            text_format={
                "type": "json_schema",
                "name": schema_name,
                "description": schema_description,
                "strict": True,
                "schema": schema,
            },
        )
        self._raise_for_refusal_or_incomplete(response, schema_name=schema_name)
        payload_text = self._extract_output_text(response).strip()
        if not payload_text:
            raise RuntimeError(
                f"OpenAI Responses returned no structured output for {schema_name!r}; "
                f"response_id={_item_get(response, 'id', '')!r}"
            )
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            snippet = payload_text[:400]
            raise RuntimeError(
                f"OpenAI Responses returned invalid JSON for {schema_name!r}; "
                f"response_id={_item_get(response, 'id', '')!r}; payload_snippet={snippet!r}"
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"OpenAI Responses returned non-object JSON for {schema_name!r}; "
                f"response_id={_item_get(response, 'id', '')!r}; payload_type={type(payload).__name__!r}"
            )
        return payload

    def _normalize_input_items(self, input_items: str | Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(input_items, str):
            return [{"role": "user", "content": input_items}]
        return [dict(item) for item in input_items]

    def _extract_output_text(self, response: Any) -> str:
        raw_output_text = _item_get(response, "output_text")
        if raw_output_text:
            return str(raw_output_text)
        parts: list[str] = []
        for item in _item_get(response, "output", []) or []:
            if _item_get(item, "type") != "message":
                continue
            for content_item in _item_get(item, "content", []) or []:
                if _item_get(content_item, "type") == "output_text":
                    parts.append(str(_item_get(content_item, "text", "")))
        return "".join(parts)

    def _extract_refusal(self, response: Any) -> str:
        for item in _item_get(response, "output", []) or []:
            if _item_get(item, "type") != "message":
                continue
            for content_item in _item_get(item, "content", []) or []:
                if _item_get(content_item, "type") == "refusal":
                    return str(_item_get(content_item, "refusal", "")).strip()
        return ""

    def _raise_for_refusal_or_incomplete(self, response: Any, *, schema_name: str) -> None:
        refusal = self._extract_refusal(response)
        if refusal:
            raise RuntimeError(
                f"OpenAI Responses refused {schema_name!r}; "
                f"response_id={_item_get(response, 'id', '')!r}; refusal={refusal!r}"
            )
        status = str(_item_get(response, "status", "")).strip().lower()
        if status == "incomplete":
            incomplete_details = _item_get(response, "incomplete_details")
            reason = _item_get(incomplete_details, "reason", "unknown")
            raise RuntimeError(
                f"OpenAI Responses returned incomplete output for {schema_name!r}; "
                f"response_id={_item_get(response, 'id', '')!r}; reason={reason!r}"
            )
