from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from .base import AdapterError, SubjectAdapter, SubjectSession
from ..models import AdapterTurnResult, SubjectSpec, TurnSeed
from ..openai_responses import (
    OpenAIResponsesClient,
    OpenAIResponsesClientConfig,
    build_web_search_tool,
    extract_response_citations,
    extract_response_source_metadata,
)


DEFAULT_CHATGPT_IDENTITY = "You are ChatGPT, a large language model trained by OpenAI."


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def build_default_chatgpt_preamble(clock: Callable[[], datetime] = _default_clock) -> str:
    today = clock().date().isoformat()
    return f"{DEFAULT_CHATGPT_IDENTITY}\nCurrent date: {today}"


def _item_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _response_text(response: Any) -> str:
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


def _response_status(response: Any) -> str:
    status = str(_item_get(response, "status", "")).strip()
    return status or "completed"


def _bool_override(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"Invalid boolean override: {value!r}")


def _raise_for_failed_response(response: Any) -> None:
    status = str(_item_get(response, "status", "")).strip().lower()
    if status != "failed":
        return
    error = _item_get(response, "error")
    message = str(_item_get(error, "message", "")).strip() or str(_item_get(response, "error_message", "")).strip()
    raise AdapterError(f"OpenAI ChatGPT request failed: {message or 'unknown error'}")


def _render_sources_block(sources: Sequence[dict[str, Any]]) -> str:
    rendered: list[str] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        url = str(source.get("url", "")).strip()
        title = str(source.get("title", "")).strip()
        filename = str(source.get("filename", "")).strip()
        label = title or filename or str(source.get("source_id", "")).strip()
        if not label and not url:
            continue
        dedupe_key = (label, url)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if url and label:
            rendered.append(f"- {label} - {url}")
        elif label:
            rendered.append(f"- {label}")
        else:
            rendered.append(f"- {url}")
    if not rendered:
        return ""
    return "\n\nSources:\n" + "\n".join(rendered)


@dataclass(frozen=True)
class OpenAIChatGPTConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    instructions: str = ""
    conversation_state_mode: str = "conversation"
    web_search_enabled: bool = True
    web_search_allowed_domains: tuple[str, ...] = ()
    web_search_user_location: dict[str, Any] | None = None
    web_search_include_sources: bool = False
    web_search_search_context_size: str | None = None
    code_interpreter_enabled: bool = True
    truncation: str | None = "auto"


class OpenAIChatGPTSession(SubjectSession):
    def __init__(
        self,
        *,
        config: OpenAIChatGPTConfig,
        benchmark_run_id: str,
        subject: SubjectSpec,
        clock: Callable[[], datetime] = _default_clock,
    ):
        self.config = self._resolve_config(config=config, subject=subject)
        self.benchmark_run_id = benchmark_run_id
        self.subject = subject
        self.clock = clock
        self.client = OpenAIResponsesClient(
            OpenAIResponsesClientConfig(
                model=self.config.model,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                request_timeout_seconds=self.config.request_timeout_seconds,
                max_output_tokens=self.config.max_output_tokens,
                reasoning_effort=self.config.reasoning_effort,
            )
        )
        self.pending_context_seed: tuple[TurnSeed, ...] = ()
        self.system_preamble_suffix = ""
        self.last_response_id = ""
        self.conversation_id = ""
        self.turn_count = 0

    def _resolve_config(self, *, config: OpenAIChatGPTConfig, subject: SubjectSpec) -> OpenAIChatGPTConfig:
        overrides = dict(subject.adapter_config or {})
        mode = str(overrides.get("conversation_state_mode", config.conversation_state_mode)).strip() or "conversation"
        if mode not in {"conversation", "response_chain"}:
            raise ValueError(f"Unsupported conversation_state_mode {mode!r}.")
        base_url_value = overrides.get("base_url", config.base_url)
        request_timeout_seconds_value = overrides.get("request_timeout_seconds", config.request_timeout_seconds)
        max_output_tokens_value = overrides.get("max_output_tokens", config.max_output_tokens)
        reasoning_effort_value = overrides.get("reasoning_effort", config.reasoning_effort)
        instructions_value = overrides.get("instructions", config.instructions)
        user_location_value = overrides.get("web_search_user_location", config.web_search_user_location)
        search_context_size_value = overrides.get("web_search_search_context_size", config.web_search_search_context_size)
        allowed_domains_value = overrides.get("web_search_allowed_domains", config.web_search_allowed_domains)
        truncation_value = overrides.get("truncation", config.truncation)
        return OpenAIChatGPTConfig(
            model=str(overrides.get("model", config.model)).strip() or config.model,
            api_key=str(overrides.get("api_key", config.api_key)).strip() or config.api_key,
            base_url=(str(base_url_value).strip() or None) if base_url_value is not None else None,
            request_timeout_seconds=(
                float(request_timeout_seconds_value)
                if request_timeout_seconds_value is not None
                else config.request_timeout_seconds
            ),
            max_output_tokens=int(max_output_tokens_value) if max_output_tokens_value is not None else None,
            reasoning_effort=(str(reasoning_effort_value).strip() or None) if reasoning_effort_value is not None else None,
            instructions=str(instructions_value or ""),
            conversation_state_mode=mode,
            web_search_enabled=_bool_override(overrides.get("web_search_enabled"), config.web_search_enabled),
            web_search_allowed_domains=tuple(
                str(domain).strip()
                for domain in (allowed_domains_value or ())
                if str(domain).strip()
            ),
            web_search_user_location=dict(user_location_value) if user_location_value is not None else None,
            web_search_include_sources=_bool_override(
                overrides.get("web_search_include_sources"),
                config.web_search_include_sources,
            ),
            web_search_search_context_size=(
                str(search_context_size_value).strip() or None
                if search_context_size_value is not None
                else None
            ),
            code_interpreter_enabled=_bool_override(
                overrides.get("code_interpreter_enabled"),
                config.code_interpreter_enabled,
            ),
            truncation=(
                (str(truncation_value).strip() or None) if truncation_value is not None else None
            ),
        )

    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        system_parts: list[str] = []
        non_system: list[TurnSeed] = []
        for turn in context_seed:
            if str(turn.role).strip().lower() == "system":
                content = str(turn.content).strip()
                if content:
                    system_parts.append(content)
            else:
                non_system.append(turn)
        if system_parts:
            addition = "\n\n".join(system_parts)
            self.system_preamble_suffix = (
                f"{self.system_preamble_suffix}\n\n{addition}".strip()
                if self.system_preamble_suffix
                else addition
            )
        self.pending_context_seed = tuple(non_system)

    def _resolved_instructions(self) -> str:
        base = self.config.instructions.strip() or build_default_chatgpt_preamble(self.clock)
        if self.system_preamble_suffix:
            return f"{base}\n\n{self.system_preamble_suffix}"
        return base

    def _response_metadata(self) -> dict[str, str]:
        return {
            "benchmark_run_id": self.benchmark_run_id,
            "subject_name": self.subject.subject_name,
            "turn_index": str(self.turn_count),
        }

    def _response_chain_input_items(self, message: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.pending_context_seed:
            for turn in self.pending_context_seed:
                items.append({"role": turn.role, "content": turn.content})
            self.pending_context_seed = ()
        items.append({"role": "user", "content": message})
        return items

    def _ensure_conversation(self) -> str:
        if self.config.conversation_state_mode != "conversation":
            return ""
        if self.conversation_id:
            self.pending_context_seed = ()
            return self.conversation_id
        items = [
            {"role": turn.role, "content": turn.content}
            for turn in self.pending_context_seed
        ]
        metadata = {
            "benchmark_run_id": self.benchmark_run_id,
            "subject_name": self.subject.subject_name,
        }
        conversation = self.client.create_conversation(items=items, metadata=metadata)
        self.pending_context_seed = ()
        self.conversation_id = str(_item_get(conversation, "id", "")).strip()
        if not self.conversation_id:
            raise AdapterError("OpenAI ChatGPT conversation creation did not return an id.")
        return self.conversation_id

    def _browser_parity_tools(self) -> tuple[list[dict[str, Any]], list[str]]:
        tools: list[dict[str, Any]] = []
        include: list[str] = []
        if self.config.web_search_enabled:
            passthrough: dict[str, Any] = {}
            if self.config.web_search_search_context_size is not None:
                passthrough["search_context_size"] = self.config.web_search_search_context_size
            tools.append(
                build_web_search_tool(
                    allowed_domains=self.config.web_search_allowed_domains,
                    user_location=self.config.web_search_user_location,
                    passthrough_config=passthrough,
                )
            )
            if self.config.web_search_include_sources:
                include.append("web_search_call.action.sources")
        if self.config.code_interpreter_enabled:
            tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
        return tools, include

    def submit_user_message(self, message: str) -> AdapterTurnResult:
        tools, include = self._browser_parity_tools()
        response_kwargs: dict[str, Any] = {
            "instructions": self._resolved_instructions(),
            "tools": tools,
            "include": include,
            "metadata": self._response_metadata(),
        }
        if self.config.truncation:
            response_kwargs["truncation"] = self.config.truncation
        if self.config.conversation_state_mode == "conversation":
            response_kwargs["conversation_id"] = self._ensure_conversation()
            response_kwargs["input_items"] = [{"role": "user", "content": message}]
        else:
            response_kwargs["input_items"] = self._response_chain_input_items(message)
            if self.last_response_id:
                response_kwargs["previous_response_id"] = self.last_response_id
        try:
            response = self.client.create_response(**response_kwargs)
        except Exception as exc:  # pragma: no cover - exercised via adapter tests
            raise AdapterError(f"OpenAI ChatGPT request failed: {exc}") from exc
        _raise_for_failed_response(response)

        assistant_text = _response_text(response).strip()
        if not assistant_text:
            raise AdapterError("OpenAI ChatGPT returned an empty assistant message.")

        citations = extract_response_citations(response)
        sources = extract_response_source_metadata(response)
        rendered_message = assistant_text
        if self.config.web_search_include_sources:
            rendered_message += _render_sources_block(sources)
        self.last_response_id = str(_item_get(response, "id", "")).strip()
        self.turn_count += 1
        return AdapterTurnResult(
            user_message=message,
            assistant_message=rendered_message,
            run_id=self.last_response_id or None,
            status=_response_status(response),
            terminal_event_type="response",
            debug={
                "response_id": self.last_response_id,
                "conversation_id": self.conversation_id,
                "turn_count": self.turn_count,
                "citations": citations,
                "sources": sources,
            },
            metadata={
                "model": self.config.model,
                "provider": "openai",
                "conversation_state_mode": self.config.conversation_state_mode,
            },
        )

    def close(self) -> dict[str, Any]:
        return {
            "last_response_id": self.last_response_id,
            "conversation_id": self.conversation_id,
            "turn_count": self.turn_count,
        }

    def abort(self) -> dict[str, Any]:
        return self.close()


class OpenAIChatGPTAdapter(SubjectAdapter):
    name = "openai_chatgpt"

    def __init__(self, config: OpenAIChatGPTConfig):
        self.config = config

    def create_session(self, benchmark_run_id: str, subject: SubjectSpec) -> SubjectSession:
        return OpenAIChatGPTSession(
            config=self.config,
            benchmark_run_id=benchmark_run_id,
            subject=subject,
        )
