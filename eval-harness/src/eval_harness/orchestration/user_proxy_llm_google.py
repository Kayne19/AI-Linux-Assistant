from __future__ import annotations

from itertools import count
from typing import Any

try:
    from google import genai
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    genai = None

from .user_proxy_llm import (
    UserProxyLLMClientConfig,
    UserProxyLLMResponse,
    UserProxyToolCall,
    _parse_tool_arguments,
)


def _render_turn_text(transcript: list[tuple[str, str]], assistant_reply: str) -> str:
    rendered = "\n".join(f"{role}: {content}" for role, content in transcript)
    if rendered:
        return f"Conversation so far:\n{rendered}\n\nAssistant just said:\n{assistant_reply}"
    return assistant_reply


def _translate_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    return [
        {
            "function_declarations": [
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                }
                for tool in tools
            ]
        }
    ]


def _response_id(response: Any, counter: count) -> str:
    raw = getattr(response, "id", None)
    if raw:
        return str(raw)
    return f"google-response-{next(counter)}"


def _tool_calls_from_response(response: Any) -> tuple[UserProxyToolCall, ...]:
    tool_calls: list[UserProxyToolCall] = []
    for call in getattr(response, "function_calls", []) or []:
        tool_calls.append(
            UserProxyToolCall(
                id=str(getattr(call, "id", "") or ""),
                name=str(getattr(call, "name", "") or ""),
                arguments=_parse_tool_arguments(getattr(call, "args", {}) or {}),
            )
        )
    return tuple(tool_calls)


def _model_content_from_response(response: Any) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    text = str(getattr(response, "text", "") or "")
    if text:
        parts.append({"text": text})
    for call in getattr(response, "function_calls", []) or []:
        parts.append(
            {
                "function_call": {
                    "name": str(getattr(call, "name", "") or ""),
                    "args": getattr(call, "args", {}) or {},
                    "id": str(getattr(call, "id", "") or ""),
                }
            }
        )
    return {"role": "model", "parts": parts}


class GoogleGenAIUserProxyLLMClient:
    def __init__(self, config: UserProxyLLMClientConfig) -> None:
        if genai is None:
            raise RuntimeError("Google GenAI SDK is not installed. Add the 'google-genai' package to use Google clients.")
        if config.base_url:
            raise ValueError("Google GenAI client does not support base_url overrides in the eval harness.")
        self.config = config
        self.client = genai.Client(
            api_key=config.api_key,
            http_options={"timeout": config.request_timeout_seconds},
        )
        self._response_counter = count(1)
        self._contents_by_response_id: dict[str, list[dict[str, Any]]] = {}

    def _request_kwargs(
        self,
        *,
        system_prompt: str,
        contents: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {"system_instruction": system_prompt}
        translated_tools = _translate_tools(tools)
        if translated_tools:
            config["tools"] = translated_tools
        if self.config.max_output_tokens is not None:
            config["max_output_tokens"] = self.config.max_output_tokens
        return {
            "model": self.config.model,
            "contents": contents,
            "config": config,
        }

    def _coerce_response(self, response: Any, *, prior_contents: list[dict[str, Any]]) -> UserProxyLLMResponse:
        response_id = _response_id(response, self._response_counter)
        history = list(prior_contents)
        history.append(_model_content_from_response(response))
        self._contents_by_response_id[response_id] = history
        tool_calls = _tool_calls_from_response(response)
        return UserProxyLLMResponse(
            content=str(getattr(response, "text", "") or ""),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            response_id=response_id,
        )

    def start_turn(
        self,
        *,
        system_prompt: str,
        transcript: list[tuple[str, str]],
        assistant_reply: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        turn_text = _render_turn_text(transcript, assistant_reply)
        response = self.client.models.generate_content(**self._request_kwargs(system_prompt=system_prompt, contents=turn_text, tools=tools))
        prior_contents = [{"role": "user", "parts": [{"text": turn_text}]}]
        return self._coerce_response(response, prior_contents=prior_contents)

    def continue_turn(
        self,
        *,
        system_prompt: str,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        contents = list(self._contents_by_response_id.get(previous_response_id) or [])
        for item in tool_outputs:
            contents.append(
                {
                    "role": "tool",
                    "parts": [
                        {
                            "function_response": {
                                "name": str(item.get("name") or "run_command"),
                                "id": str(item.get("call_id", "") or ""),
                                "response": {"output": item.get("output", "")},
                            }
                        }
                    ],
                }
            )
        response = self.client.models.generate_content(**self._request_kwargs(system_prompt=system_prompt, contents=contents, tools=tools))
        return self._coerce_response(response, prior_contents=contents)
