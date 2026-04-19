from __future__ import annotations

from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    Anthropic = None

from .user_proxy_llm import (
    UserProxyLLMClientConfig,
    UserProxyLLMResponse,
    UserProxyReplyReview,
    UserProxyToolCall,
    _REVIEW_SYSTEM_PROMPT,
    _parse_tool_arguments,
    build_review_input,
    build_proxy_native_history,
    build_retry_review_input,
    parse_review_payload,
)


def _translate_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    translated: list[dict[str, Any]] = []
    for tool in tools or []:
        translated.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
            }
        )
    return translated


def _assistant_message_from_response(response: Any) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            content.append({"type": "text", "text": str(getattr(block, "text", "") or "")})
        elif block_type == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": str(getattr(block, "id", "") or ""),
                    "name": str(getattr(block, "name", "") or ""),
                    "input": getattr(block, "input", {}) or {},
                }
            )
    return {"role": "assistant", "content": content}


def _tool_calls_from_response(response: Any) -> tuple[UserProxyToolCall, ...]:
    tool_calls: list[UserProxyToolCall] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) != "tool_use":
            continue
        tool_calls.append(
            UserProxyToolCall(
                id=str(getattr(block, "id", "") or ""),
                name=str(getattr(block, "name", "") or ""),
                arguments=_parse_tool_arguments(getattr(block, "input", {}) or {}),
            )
        )
    return tuple(tool_calls)


def _text_from_response(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text = str(getattr(block, "text", "") or "")
            if text:
                parts.append(text)
    return "".join(parts)


class AnthropicUserProxyLLMClient:
    def __init__(self, config: UserProxyLLMClientConfig) -> None:
        if Anthropic is None:
            raise RuntimeError("Anthropic SDK is not installed. Add the 'anthropic' package to use Anthropic clients.")
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "timeout": config.request_timeout_seconds,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self.config = config
        self.client = Anthropic(**client_kwargs)
        self._messages_by_response_id: dict[str, list[dict[str, Any]]] = {}

    def _request_kwargs(self, *, system_prompt: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": self.config.max_output_tokens or 4096,
        }
        translated_tools = _translate_tools(tools)
        if translated_tools:
            kwargs["tools"] = translated_tools
        return kwargs

    def _coerce_response(self, response: Any, *, prior_messages: list[dict[str, Any]]) -> UserProxyLLMResponse:
        response_id = str(getattr(response, "id", "") or "")
        history = list(prior_messages)
        history.append(_assistant_message_from_response(response))
        self._messages_by_response_id[response_id] = history
        tool_calls = _tool_calls_from_response(response)
        content = _text_from_response(response)
        return UserProxyLLMResponse(
            content=content,
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
        recent_memory_text: str | None = None,
    ) -> UserProxyLLMResponse:
        pairs = build_proxy_native_history(
            transcript, assistant_reply, recent_memory_text=recent_memory_text
        )
        messages = [{"role": role, "content": content} for role, content in pairs]
        response = self.client.messages.create(**self._request_kwargs(system_prompt=system_prompt, messages=messages, tools=tools))
        return self._coerce_response(response, prior_messages=messages)

    def continue_turn(
        self,
        *,
        system_prompt: str,
        previous_response_id: str,
        tool_outputs: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> UserProxyLLMResponse:
        messages = list(self._messages_by_response_id.get(previous_response_id) or [])
        tool_result_blocks = []
        for item in tool_outputs:
            tool_result_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": item.get("call_id"),
                    "content": str(item.get("output", "")),
                }
            )
        messages.append({"role": "user", "content": tool_result_blocks})
        response = self.client.messages.create(**self._request_kwargs(system_prompt=system_prompt, messages=messages, tools=tools))
        return self._coerce_response(response, prior_messages=messages)

    def review_reply(
        self,
        *,
        system_prompt: str,
        transcript: list[tuple[str, str]],
        subject_reply: str,
        recent_memory_text: str | None,
        tool_outputs_text: list[str],
        tool_names_used_this_turn: list[str],
        draft_reply: str,
    ) -> UserProxyReplyReview:
        """Always-on revision pass: ask the model to return a structured review."""
        review_input = build_review_input(
            subject_reply=subject_reply,
            recent_memory_text=recent_memory_text,
            tool_outputs_text=tool_outputs_text,
            tool_names_used_this_turn=tool_names_used_this_turn,
            draft_reply=draft_reply,
        )

        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=_REVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": review_input}],
                max_tokens=self.config.max_output_tokens or 512,
            )
            return parse_review_payload(_text_from_response(response).strip(), draft_reply=draft_reply)
        except Exception:  # noqa: BLE001
            return parse_review_payload("", draft_reply=draft_reply)

    def retry_turn(
        self,
        *,
        system_prompt: str,
        transcript: list[tuple[str, str]],
        assistant_reply: str,
        tools: list[dict[str, Any]] | None = None,
        recent_memory_text: str | None = None,
        draft_reply: str,
        review_verdict: str,
        review_reason: str,
        tool_names_used_this_turn: list[str] | None = None,
        tool_outputs_text: list[str] | None = None,
    ) -> UserProxyLLMResponse:
        pairs = build_proxy_native_history(
            transcript, assistant_reply, recent_memory_text=recent_memory_text
        )
        messages = [{"role": role, "content": content} for role, content in pairs]
        messages.append(
            {
                "role": "user",
                "content": build_retry_review_input(
                    subject_reply=assistant_reply,
                    recent_memory_text=recent_memory_text,
                    tool_outputs_text=tool_outputs_text or [],
                    tool_names_used_this_turn=tool_names_used_this_turn or [],
                    draft_reply=draft_reply,
                    review_verdict=review_verdict,
                    review_reason=review_reason,
                ),
            }
        )
        response = self.client.messages.create(
            **self._request_kwargs(system_prompt=system_prompt, messages=messages, tools=tools)
        )
        return self._coerce_response(response, prior_messages=messages)
