"""Responses-based OpenAI client for the eval-harness user proxy FSM."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised only when dependency missing
    OpenAI = None


@dataclass(frozen=True)
class UserProxyLLMClientConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class UserProxyToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class UserProxyLLMResponse:
    content: str
    tool_calls: tuple[UserProxyToolCall, ...]
    finish_reason: str
    response_id: str


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_tool_calls(response: Any) -> tuple[UserProxyToolCall, ...]:
    result: list[UserProxyToolCall] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        result.append(
            UserProxyToolCall(
                id=str(getattr(item, "call_id", "")),
                name=str(getattr(item, "name", "")),
                arguments=_parse_tool_arguments(getattr(item, "arguments", {})),
            )
        )
    return tuple(result)


def _extract_refusal_text(response: Any) -> str:
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content_item in getattr(item, "content", []) or []:
            if getattr(content_item, "type", None) == "refusal":
                return str(getattr(content_item, "refusal", "") or "").strip()
    return ""


def _finish_reason_for_response(response: Any, tool_calls: tuple[UserProxyToolCall, ...], content: str) -> str:
    if tool_calls:
        return "tool_calls"
    if _extract_refusal_text(response):
        return "refusal"
    if content:
        return "stop"
    status = str(getattr(response, "status", "") or "").strip()
    return status or "stop"


class UserProxyLLMClient:
    """OpenAI Responses client for the confused-user proxy turn loop."""

    def __init__(self, config: UserProxyLLMClientConfig) -> None:
        if OpenAI is None:
            raise RuntimeError("OpenAI SDK is not installed. Install the 'openai' package to use the user proxy LLM.")
        self.config = config
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "timeout": config.request_timeout_seconds,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.client = OpenAI(**client_kwargs)

    def _request_kwargs(
        self,
        *,
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": system_prompt,
        }
        if self.config.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.config.max_output_tokens
        if self.config.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": self.config.reasoning_effort}
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = True
        return kwargs

    def _coerce_response(self, response: Any) -> UserProxyLLMResponse:
        if getattr(response, "error", None) is not None:
            raise RuntimeError(f"User proxy model call failed: {response.error}")
        if getattr(response, "status", None) == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", "unknown") if details is not None else "unknown"
            raise RuntimeError(f"User proxy model response incomplete: {reason}")

        tool_calls = _extract_tool_calls(response)
        content = str(getattr(response, "output_text", "") or "")
        if not content:
            content = _extract_refusal_text(response)
        return UserProxyLLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=_finish_reason_for_response(response, tool_calls, content),
            response_id=str(getattr(response, "id", "") or ""),
        )

    def start_turn(
        self,
        *,
        system_prompt: str,
        transcript: list[tuple[str, str]],
        assistant_reply: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        rendered = "\n".join(f"{role}: {content}" for role, content in transcript)
        if rendered:
            turn_text = f"Conversation so far:\n{rendered}\n\nAssistant just said:\n{assistant_reply}"
        else:
            turn_text = assistant_reply
        request_kwargs = self._request_kwargs(system_prompt=system_prompt, tools=tools)
        request_kwargs["input"] = [{"role": "user", "content": turn_text}]
        response = self.client.responses.create(**request_kwargs)
        return self._coerce_response(response)

    def continue_turn(
        self,
        *,
        system_prompt: str,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        request_kwargs = self._request_kwargs(system_prompt=system_prompt, tools=tools)
        request_kwargs["previous_response_id"] = previous_response_id
        request_kwargs["input"] = list(tool_outputs)
        response = self.client.responses.create(**request_kwargs)
        return self._coerce_response(response)
