"""Responses-based OpenAI client for the eval-harness user proxy FSM."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

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
    """Structured result of the always-on revision pass for a proxy draft reply."""

    verdict: Literal["accept", "rewrite_text", "retry_with_tools"]
    final_reply: str
    reason: str
    issues: tuple[str, ...] = ()
    audit_json: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserProxyReplyReview":
        verdict = str(payload.get("verdict", "")).strip().lower()
        if verdict not in {"accept", "rewrite_text", "retry_with_tools"}:
            raise ValueError(
                "structured output field 'verdict' must be one of: "
                "accept, rewrite_text, retry_with_tools"
            )
        final_reply = str(payload.get("final_reply", "") or "").strip()
        if not final_reply:
            raise ValueError("structured output field 'final_reply' must be non-empty")
        reason = str(payload.get("reason", "") or "reviewed").strip() or "reviewed"
        raw_issues = payload.get("issues", ())
        if isinstance(raw_issues, (list, tuple)):
            issues = tuple(str(item) for item in raw_issues if str(item).strip())
        else:
            issues = ()
        raw_audit = payload.get("audit_json", payload.get("audit", {}))
        audit_json = dict(raw_audit or {}) if isinstance(raw_audit, dict) else {}
        return cls(
            verdict=verdict,  # type: ignore[arg-type]
            final_reply=final_reply,
            reason=reason,
            issues=issues,
            audit_json=audit_json,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "final_reply": self.final_reply,
            "reason": self.reason,
            "issues": list(self.issues),
            "audit_json": dict(self.audit_json),
        }


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
    "The assistant is text-only: it cannot run commands, inspect files, or edit the machine itself. "
    "The proxy is the human user with terminal access. Your job is not to choose strategy. "
    "The draft generator already decided whether to use tools or ask a question. "
    "Your job is only to enforce two things: "
    "(1) the reply must stay in human user voice rather than assistant/helpdesk voice, and "
    "(2) the proxy must have used tools properly for the assistant's request. "
    "A draft is acceptable only if it either actually acted appropriately, accurately answers from already-observed evidence, or asked a human-sounding question that fits the assistant's request. "
    "A draft is not acceptable if it merely repeats the diagnosis, restates the assistant's instruction without acting, or uses tools incorrectly. "
    "Breaking character is a reason to rewrite or retry.\n\n"
    "Fix any wording issues: remove assistant-like directives (do not say 'you should', "
    "'please run', 'let me know', or similar), ensure the reply accurately reports terminal "
    "output rather than fabricating it, and write in first-person confused-user voice. "
    "Do not tell the assistant what to run. Do not ask the assistant to paste output. "
    "Do not sound like a troubleshooter, operator, or helpdesk agent. "
    "If the assistant asked for logs, exact output, or command output, return the evidence only "
    "using the terminal output that was actually observed; do not diagnose the issue, do not say "
    "'that's why', do not identify a root cause, and do not propose the next fix.\n\n"
    "Bad example:\n"
    "Assistant: Please show me the output of systemctl status nginx.\n"
    "Draft: Run systemctl status nginx and paste the output so we can see what's wrong.\n"
    "Why bad: It breaks character, tells the assistant what to run, and ignores that the proxy has terminal access.\n\n"
    "Good example:\n"
    "Assistant: Please show me the output of systemctl status nginx.\n"
    "Draft: I ran that. It says nginx.service failed to start because the config test failed.\n\n"
    "Bad example:\n"
    "Assistant: Check /etc/nginx/nginx.conf and remove the duplicate include line.\n"
    "Draft: You should remove the duplicate line and then rerun nginx -t.\n"
    "Why bad: It switches into assistant voice instead of acting or asking for a real clarification.\n\n"
    "Good example:\n"
    "Assistant: Check /etc/nginx/nginx.conf and remove the duplicate include line.\n"
    "Draft: I removed that duplicate include line from /etc/nginx/nginx.conf.\n\n"
    "A draft is not acceptable if it promises an action the proxy cannot take in this conversation — "
    "checking from another computer, asking another person, calling someone, physically inspecting hardware, "
    "or saying it will check / get back / report later. Use retry_with_tools so the proxy regenerates a reply "
    "that either acts now from this terminal or tells the assistant plainly that the action isn't possible from here. "
    "The proxy does not have to solve the assistant's request — it just has to be honest about what it can and can't do. "
    "If the assistant keeps asking for the impossible after being told, that is the assistant's problem, "
    "not a reason to fabricate compliance.\n\n"
    "Bad example:\n"
    "Assistant: Can you check if the site loads from another computer?\n"
    "Draft: I'll check in a minute.\n"
    "Why bad: It promises a future out-of-band action the proxy cannot take — there is no other computer in this conversation.\n\n"
    "Good example:\n"
    "Assistant: Can you check if the site loads from another computer?\n"
    "Draft: I can't try it from another computer — I only have this terminal.\n\n"
    "Verdict rules:\n"
    "- accept: the draft already sounds like a human user and tool use was appropriate.\n"
    "- rewrite_text: the underlying action/question is fine, but the wording sounds assistant-like or unnatural. Rewrite the text only; do not change the action.\n"
    "- retry_with_tools: the proxy used tools incorrectly, failed to use tools when it should have, or otherwise needs a fresh retry.\n\n"
    "Return ONLY valid JSON with this shape: "
    "{\"final_reply\": string, \"verdict\": \"accept\"|\"rewrite_text\"|\"retry_with_tools\", "
    "\"reason\": string, "
    "\"issues\": string[], "
    "\"audit_json\": {"
    "\"assistant_instruction_type\": string, "
    "\"tool_use_summary\": string, "
    "\"acceptable_tool_use\": boolean, "
    "\"character_ok\": boolean, "
    "\"character_issue\": string, "
    "\"voice_issue_examples\": string[], "
    "\"reasoning\": string, "
    "\"expected_next_action\": string, "
    "\"why_retry_or_clarify\": string, "
    "\"edited_reply\": boolean"
    "}}."
)

_VALID_REVIEW_VERDICTS = frozenset({"accept", "rewrite_text", "retry_with_tools"})


def _default_review_audit() -> dict[str, Any]:
    return {
        "assistant_instruction_type": "",
        "tool_use_summary": "",
        "acceptable_tool_use": True,
        "character_ok": True,
        "character_issue": "",
        "voice_issue_examples": [],
        "reasoning": "",
        "expected_next_action": "",
        "why_retry_or_clarify": "",
        "edited_reply": False,
    }


def build_review_input(
    *,
    subject_reply: str,
    recent_memory_text: str | None,
    tool_outputs_text: list[str],
    tool_names_used_this_turn: list[str],
    draft_reply: str,
) -> str:
    context_parts = [f"[Assistant message]\n{subject_reply}"]
    if recent_memory_text:
        context_parts.append(f"[Recent terminal actions]\n{recent_memory_text}")
    if tool_outputs_text:
        combined = "\n\n".join(tool_outputs_text)
        context_parts.append(f"[Terminal output this turn]\n{combined}")
    context_parts.append(
        "[Tool names used this turn]\n"
        + (", ".join(tool_names_used_this_turn) if tool_names_used_this_turn else "(none)")
    )
    context_parts.append(f"[Draft reply]\n{draft_reply}")
    return "\n\n".join(context_parts)


def build_retry_review_input(
    *,
    subject_reply: str,
    recent_memory_text: str | None,
    tool_outputs_text: list[str],
    tool_names_used_this_turn: list[str],
    draft_reply: str,
    review_verdict: str,
    review_reason: str,
) -> str:
    context_parts = [
        "[Proxy review verdict]\n"
        f"{review_verdict}\n\n"
        "[Proxy review reason]\n"
        f"{review_reason}"
    ]
    context_parts.append(build_review_input(
        subject_reply=subject_reply,
        recent_memory_text=recent_memory_text,
        tool_outputs_text=tool_outputs_text,
        tool_names_used_this_turn=tool_names_used_this_turn,
        draft_reply=draft_reply,
    ))
    context_parts.append(
        "Try again from the top of this turn. If you need tools, use them now. "
        "Otherwise return the best proxy reply directly."
    )
    return "\n\n".join(context_parts)


def parse_review_payload(payload_text: str, *, draft_reply: str) -> UserProxyReplyReview:
    try:
        payload = json.loads(payload_text)
    except Exception:  # noqa: BLE001
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    final_reply = str(payload.get("final_reply", "") or "").strip() or draft_reply
    verdict = str(payload.get("verdict", "accept") or "accept").strip()
    if verdict not in _VALID_REVIEW_VERDICTS:
        verdict = "accept"
    reason = str(payload.get("reason", "") or "").strip() or "reviewed"
    raw_issues = payload.get("issues", ())
    if isinstance(raw_issues, (list, tuple)):
        issues = tuple(str(item) for item in raw_issues if str(item).strip())
    else:
        issues = ()

    audit = _default_review_audit()
    raw_audit = payload.get("audit_json", payload.get("audit", {}))
    if isinstance(raw_audit, dict):
        audit.update(raw_audit)
    audit["edited_reply"] = bool(audit.get("edited_reply")) or final_reply.strip() != draft_reply.strip()
    audit["acceptable_tool_use"] = bool(audit.get("acceptable_tool_use"))

    return UserProxyReplyReview(
        verdict=verdict,  # type: ignore[arg-type]
        final_reply=final_reply,
        reason=reason,
        issues=issues,
        audit_json=audit,
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
        tool_names_used_this_turn: list[str],
        draft_reply: str,
    ) -> UserProxyReplyReview:
        """Always-on revision pass: ask the model to return a structured review."""
        del system_prompt, transcript
        review_input = build_review_input(
            subject_reply=subject_reply,
            recent_memory_text=recent_memory_text,
            tool_outputs_text=tool_outputs_text,
            tool_names_used_this_turn=tool_names_used_this_turn,
            draft_reply=draft_reply,
        )

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": _REVIEW_SYSTEM_PROMPT,
            "input": [{"role": "user", "content": review_input}],
        }
        if self.config.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.config.max_output_tokens
        try:
            response = self.client.responses.create(**kwargs)
            payload_text = str(getattr(response, "output_text", "") or "").strip()
            return parse_review_payload(payload_text, draft_reply=draft_reply)
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
        retry_input = build_retry_review_input(
            subject_reply=assistant_reply,
            recent_memory_text=recent_memory_text,
            tool_outputs_text=tool_outputs_text or [],
            tool_names_used_this_turn=tool_names_used_this_turn or [],
            draft_reply=draft_reply,
            review_verdict=review_verdict,
            review_reason=review_reason,
        )
        request_kwargs = self._request_kwargs(system_prompt=system_prompt, tools=tools)
        request_kwargs["input"] = [{"role": role, "content": content} for role, content in build_proxy_native_history(
            transcript,
            assistant_reply,
            recent_memory_text=recent_memory_text,
        )]
        request_kwargs["input"].append({"role": "user", "content": retry_input})
        response = self.client.responses.create(**request_kwargs)
        return self._coerce_response(response)
