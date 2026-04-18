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


@dataclass(frozen=True)
class UserProxyReplyReview:
    """Result of the always-on revision pass for a proxy draft reply."""
    final_reply: str
    issues: tuple[str, ...]


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


def _validate_openai_strict_tool_schema(schema: Any, *, tool_name: str, path: str = "parameters") -> None:
    if not isinstance(schema, dict):
        return

    schema_type = schema.get("type")
    if schema_type == "object":
        if schema.get("additionalProperties") is not False:
            raise ValueError(
                f"OpenAI strict tool schema for {tool_name!r} must set additionalProperties=false at {path}"
            )
        for property_name, property_schema in (schema.get("properties") or {}).items():
            _validate_openai_strict_tool_schema(
                property_schema,
                tool_name=tool_name,
                path=f"{path}.properties.{property_name}",
            )

    if schema_type == "array":
        _validate_openai_strict_tool_schema(
            schema.get("items"),
            tool_name=tool_name,
            path=f"{path}.items",
        )

    for keyword in ("anyOf", "allOf", "oneOf"):
        for index, nested_schema in enumerate(schema.get(keyword) or []):
            _validate_openai_strict_tool_schema(
                nested_schema,
                tool_name=tool_name,
                path=f"{path}.{keyword}[{index}]",
            )


def _validate_openai_tools(tools: list[dict[str, Any]] | None) -> None:
    for tool in tools or []:
        if tool.get("type") != "function" or not tool.get("strict"):
            continue
        _validate_openai_strict_tool_schema(
            tool.get("parameters"),
            tool_name=str(tool.get("name", "unknown_tool")),
        )


def build_proxy_native_history(
    transcript: list[tuple[str, str]],
    subject_reply: str,
    *,
    recent_memory_text: str | None = None,
) -> list[tuple[str, str]]:
    """Build proxy-relative (role, content) pairs for the LLM.

    Transcript is from the benchmark perspective:
    - ("user", content)      = prior proxy reply
    - ("assistant", content) = subject (assistant) reply

    For the proxy's view, roles are flipped:
    - subject replies  → "user"  (what the proxy receives)
    - proxy replies    → "assistant" (what the proxy previously said)

    Leading proxy turns (before any subject reply) are skipped because
    the opening message is already covered by the system prompt's problem
    statement. This also avoids starting native history with an "assistant"
    turn, which some providers reject.

    The current subject_reply is appended as the final "user" turn.
    If recent_memory_text is provided it is appended to that final message
    as a labelled context block.
    """
    # Skip leading proxy turns that precede the first subject reply.
    i = 0
    while i < len(transcript) and transcript[i][0] == "user":
        i += 1

    pairs: list[tuple[str, str]] = []
    for role, content in transcript[i:]:
        proxy_role = "user" if role == "assistant" else "assistant"
        pairs.append((proxy_role, content))

    # Build final user turn, optionally including recent terminal memory.
    final_content = subject_reply
    if recent_memory_text:
        final_content = f"{subject_reply}\n\n[Recent terminal actions]\n{recent_memory_text}"
    pairs.append(("user", final_content))
    return pairs


_REVIEW_SYSTEM_PROMPT = (
    "You are reviewing a draft reply from someone simulating a confused Linux user. "
    "The user is at a terminal and an assistant is trying to help them fix a problem. "
    "Fix any issues in the draft: remove assistant-like directives (do not say 'you should', "
    "'please run', 'let me know', or similar), ensure the reply accurately reports terminal "
    "output rather than fabricating it, and write in first-person confused-user voice. "
    "Return ONLY the corrected reply text with no explanation or preamble."
)


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
            _validate_openai_tools(tools)
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
        recent_memory_text: str | None = None,
    ) -> UserProxyLLMResponse:
        pairs = build_proxy_native_history(
            transcript, assistant_reply, recent_memory_text=recent_memory_text
        )
        request_kwargs = self._request_kwargs(system_prompt=system_prompt, tools=tools)
        request_kwargs["input"] = [{"role": role, "content": content} for role, content in pairs]
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

    def review_reply(
        self,
        *,
        system_prompt: str,
        transcript: list[tuple[str, str]],
        subject_reply: str,
        recent_memory_text: str | None,
        tool_outputs_text: list[str],
        draft_reply: str,
    ) -> UserProxyReplyReview:
        """Always-on revision pass: ask the model to fix the draft reply."""
        context_parts = [f"[Assistant message]\n{subject_reply}"]
        if recent_memory_text:
            context_parts.append(f"[Recent terminal actions]\n{recent_memory_text}")
        if tool_outputs_text:
            combined = "\n\n".join(tool_outputs_text)
            context_parts.append(f"[Terminal output this turn]\n{combined}")
        context_parts.append(f"[Draft reply]\n{draft_reply}")
        review_input = "\n\n".join(context_parts)

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": _REVIEW_SYSTEM_PROMPT,
            "input": [{"role": "user", "content": review_input}],
        }
        if self.config.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.config.max_output_tokens
        try:
            response = self.client.responses.create(**kwargs)
            final = str(getattr(response, "output_text", "") or "").strip()
        except Exception:  # noqa: BLE001
            final = draft_reply
        return UserProxyReplyReview(final_reply=final or draft_reply, issues=())
