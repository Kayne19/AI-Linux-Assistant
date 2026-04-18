from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import AdapterError, SubjectAdapter, SubjectSession
from ..models import AdapterTurnResult, SubjectSpec, TurnSeed
from ..openai_responses import OpenAIResponsesClient, OpenAIResponsesClientConfig


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


@dataclass(frozen=True)
class OpenAIChatGPTConfig:
    model: str
    api_key: str
    base_url: str | None = None
    request_timeout_seconds: float = 60.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


class OpenAIChatGPTSession(SubjectSession):
    def __init__(
        self,
        *,
        config: OpenAIChatGPTConfig,
        benchmark_run_id: str,
        subject: SubjectSpec,
    ):
        self.config = self._resolve_config(config=config, subject=subject)
        self.benchmark_run_id = benchmark_run_id
        self.subject = subject
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
        self.last_response_id = ""
        self.turn_count = 0

    def _resolve_config(self, *, config: OpenAIChatGPTConfig, subject: SubjectSpec) -> OpenAIChatGPTConfig:
        overrides = dict(subject.adapter_config or {})
        base_url_value = overrides.get("base_url", config.base_url)
        request_timeout_seconds_value = overrides.get("request_timeout_seconds", config.request_timeout_seconds)
        max_output_tokens_value = overrides.get("max_output_tokens", config.max_output_tokens)
        reasoning_effort_value = overrides.get("reasoning_effort", config.reasoning_effort)
        return OpenAIChatGPTConfig(
            model=str(overrides.get("model", config.model)).strip() or config.model,
            api_key=str(overrides.get("api_key", config.api_key)).strip() or config.api_key,
            base_url=(str(base_url_value).strip() or None) if base_url_value is not None else None,
            request_timeout_seconds=float(
                request_timeout_seconds_value if request_timeout_seconds_value is not None else config.request_timeout_seconds
            ),
            max_output_tokens=int(max_output_tokens_value) if max_output_tokens_value is not None else None,
            reasoning_effort=(str(reasoning_effort_value).strip() or None) if reasoning_effort_value is not None else None,
        )

    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        self.pending_context_seed = tuple(context_seed)

    def _input_items_for_message(self, message: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.pending_context_seed:
            for turn in self.pending_context_seed:
                items.append({"role": turn.role, "content": turn.content})
            self.pending_context_seed = ()
        items.append({"role": "user", "content": message})
        return items

    def submit_user_message(self, message: str) -> AdapterTurnResult:
        input_items = self._input_items_for_message(message)
        try:
            response = self.client.create_response(
                instructions="",
                input_items=input_items,
                previous_response_id=self.last_response_id or None,
            )
        except Exception as exc:  # pragma: no cover - exercised via adapter tests
            raise AdapterError(f"OpenAI ChatGPT request failed: {exc}") from exc

        assistant_message = _response_text(response).strip()
        if not assistant_message:
            raise AdapterError("OpenAI ChatGPT returned an empty assistant message.")

        self.last_response_id = str(_item_get(response, "id", "")).strip()
        self.turn_count += 1
        return AdapterTurnResult(
            user_message=message,
            assistant_message=assistant_message,
            run_id=self.last_response_id or None,
            status=_response_status(response),
            terminal_event_type="response",
            debug={
                "response_id": self.last_response_id,
                "turn_count": self.turn_count,
                "seed_count": len(input_items) - 1,
            },
            metadata={
                "model": self.config.model,
                "provider": "openai",
            },
        )

    def close(self) -> dict[str, Any]:
        return {
            "last_response_id": self.last_response_id,
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
