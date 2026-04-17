"""UserProxyLLMClient — thin OpenAI-compatible HTTP client with tool-calling support.

Used by UserProxyFSM to drive the "confused human user" persona.  Models HTTP
shape on planners/openai_compatible.py but adds a tools parameter and returns
structured tool-call results.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserProxyLLMClientConfig:
    base_url: str
    model: str
    api_key: str
    request_timeout_seconds: float = 60.0
    max_output_tokens: int = 1024


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(payload: dict[str, Any]) -> str:
    """Extract text content from an OpenAI-compatible response, handling list content."""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _extract_tool_calls(payload: dict[str, Any]) -> tuple[UserProxyToolCall, ...]:
    """Extract tool calls from an OpenAI-compatible response."""
    choices = payload.get("choices") or []
    if not choices:
        return ()
    message = choices[0].get("message") or {}
    raw_tool_calls = message.get("tool_calls") or []
    result: list[UserProxyToolCall] = []
    for tc in raw_tool_calls:
        tc_id = str(tc.get("id", ""))
        func = tc.get("function") or {}
        name = str(func.get("name", ""))
        raw_args = func.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {"raw": raw_args}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            arguments = {}
        result.append(UserProxyToolCall(id=tc_id, name=name, arguments=arguments))
    return tuple(result)


def _extract_finish_reason(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return str(choices[0].get("finish_reason", "") or "")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class UserProxyLLMClient:
    """OpenAI-compatible HTTP client with tool-calling support.

    Carries full conversation history across turns — the caller appends tool
    results as role='tool' messages and calls chat() again for the next DECIDE.
    """

    def __init__(self, config: UserProxyLLMClientConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        """Send a chat request and return a structured response."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": self.config.max_output_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        response = self.session.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            json=body,
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()

        return UserProxyLLMResponse(
            content=_extract_text(payload),
            tool_calls=_extract_tool_calls(payload),
            finish_reason=_extract_finish_reason(payload),
        )
