from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import requests

from .auth0_m2m import Auth0M2MConfig, Auth0M2MTokenProvider, ClientCreds
from .base import AdapterError, SubjectAdapter, SubjectSession
from ..models import AdapterTurnResult, RunEvent, RunEventType, SubjectSpec, TurnSeed

TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _extract_message_text(message_payload: dict[str, Any]) -> str:
    content = message_payload.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        return "".join(text_parts)
    return str(content)


def _event_code(payload: dict[str, Any], event_type: RunEventType) -> str:
    if event_type == RunEventType.DONE:
        return "done"
    return str(payload.get("code", "")).strip() or event_type.value


def _event_payload(payload: dict[str, Any], event_type: RunEventType) -> dict[str, Any]:
    if event_type == RunEventType.STATE:
        return {}
    if event_type == RunEventType.EVENT:
        return dict(payload.get("payload", {}) or {})
    copied = dict(payload)
    copied.pop("type", None)
    copied.pop("seq", None)
    copied.pop("code", None)
    copied.pop("created_at", None)
    return copied


@dataclass(frozen=True)
class AILinuxAssistantHttpConfig:
    base_url: str
    auth0_m2m: Auth0M2MConfig
    request_timeout_seconds: float = 30.0
    poll_interval_seconds: float = 1.0
    poll_timeout_seconds: float = 1800.0
    project_name_prefix: str = "eval-harness"

    def __post_init__(self) -> None:
        base_url = self.base_url.strip().rstrip("/")
        object.__setattr__(self, "base_url", base_url)
        if not base_url:
            raise ValueError("base_url is required")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than 0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than 0")
        if self.poll_timeout_seconds <= 0:
            raise ValueError("poll_timeout_seconds must be greater than 0")


class AILinuxAssistantHttpSession(SubjectSession):
    def __init__(
        self,
        *,
        client: requests.Session,
        config: AILinuxAssistantHttpConfig,
        benchmark_run_id: str,
        subject: SubjectSpec,
    ):
        self.client = client
        self.config = config
        self.benchmark_run_id = benchmark_run_id
        self.subject = subject
        self.project_id = ""
        self.chat_id = ""
        self.turn_counter = 0
        self.pending_context_seed: tuple[TurnSeed, ...] = ()
        self.seed_strategy = "none"
        self.latest_run_id = ""
        self._token_provider = self._build_token_provider()
        self.default_mode = str(
            self.subject.adapter_config.get("magi_mode", self.subject.metadata.get("magi_mode", "off"))
        ).strip() or "off"
        self._ensure_workspace()

    def _build_token_provider(self) -> Auth0M2MTokenProvider:
        m2m = self.config.auth0_m2m
        subject_name = self.subject.subject_name
        creds = m2m.clients_by_subject.get(subject_name)
        if creds is None:
            configured = list(m2m.clients_by_subject.keys())
            raise AdapterError(
                f"Subject {subject_name!r} is not configured in auth0_m2m.clients_by_subject. "
                f"Configured subjects: {configured}. "
                "Add client_id/client_secret for this subject to the config."
            )
        return Auth0M2MTokenProvider(
            token_url=m2m.token_url,
            audience=m2m.audience,
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            scope=m2m.scope,
            organization=m2m.organization,
            refresh_skew_seconds=m2m.refresh_skew_seconds,
        )

    def _headers(self) -> dict[str, str]:
        token = self._token_provider.get_access_token()
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def _request_json(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.base_url}/{path.lstrip('/')}"
        try:
            response = self.client.request(
                method.upper(),
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AdapterError(f"Request failed for {method.upper()} {path}: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text.strip()
            raise AdapterError(f"HTTP {response.status_code} for {method.upper()} {path}: {detail}")

        if not response.text.strip():
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise AdapterError(f"Expected JSON from {method.upper()} {path}, got {response.text[:200]!r}") from exc

    def _ensure_workspace(self) -> None:
        project_payload = self._request_json(
            "POST",
            "/projects",
            payload={
                "name": f"{self.config.project_name_prefix}-{self.benchmark_run_id}-{self.subject.subject_name}"[:200],
                "description": f"Eval harness subject session for {self.subject.subject_name}",
            },
        )
        self.project_id = str(project_payload.get("id", "")).strip()
        if not self.project_id:
            raise AdapterError("Project creation did not return an id.")

        chat_payload = self._request_json(
            "POST",
            f"/projects/{self.project_id}/chats",
            payload={"title": f"{self.subject.subject_name}-{self.benchmark_run_id}"[:255]},
        )
        self.chat_id = str(chat_payload.get("id", "")).strip()
        if not self.chat_id:
            raise AdapterError("Chat creation did not return an id.")

    def seed_context(self, context_seed: tuple[TurnSeed, ...]) -> None:
        self.pending_context_seed = tuple(context_seed)
        self.seed_strategy = "message_preamble" if self.pending_context_seed else "none"

    def _message_with_seed(self, message: str) -> str:
        if self.pending_context_seed:
            rendered_turns = "\n".join(f"{turn.role}: {turn.content}" for turn in self.pending_context_seed)
            self.pending_context_seed = ()
            return "\n\n".join(
                [
                    "Use this benchmark context as prior conversation state. "
                    "Do not repeat it back unless it matters to solving the task.",
                    rendered_turns,
                    "Current user request:",
                    message,
                ]
            )
        return message

    def _run_event_from_api(self, payload: dict[str, Any]) -> RunEvent:
        event_type = RunEventType(str(payload.get("type", RunEventType.EVENT.value)))
        return RunEvent(
            seq=int(payload.get("seq", 0)),
            event_type=event_type,
            code=_event_code(payload, event_type),
            payload=_event_payload(payload, event_type),
            created_at=str(payload.get("created_at", "")),
        )

    def _fetch_events_after(self, run_id: str, after_seq: int) -> tuple[list[RunEvent], int]:
        payload = self._request_json("GET", f"/runs/{run_id}/events?after_seq={after_seq}&limit=1000")
        if not isinstance(payload, list):
            raise AdapterError(f"Expected a list of run events for run {run_id}.")
        events = [self._run_event_from_api(item) for item in payload if isinstance(item, dict)]
        next_seq = after_seq
        for item in events:
            next_seq = max(next_seq, item.seq)
        return events, next_seq

    def _wait_for_terminal_run(self, run_id: str) -> tuple[dict[str, Any], tuple[RunEvent, ...]]:
        deadline = time.time() + self.config.poll_timeout_seconds
        after_seq = 0
        events: list[RunEvent] = []

        while time.time() < deadline:
            new_events, after_seq = self._fetch_events_after(run_id, after_seq)
            events.extend(new_events)
            run_payload = self._request_json("GET", f"/runs/{run_id}")
            status = str(run_payload.get("status", "")).strip()
            if status in TERMINAL_RUN_STATUSES:
                tail_events, after_seq = self._fetch_events_after(run_id, after_seq)
                if tail_events:
                    events.extend(tail_events)
                return run_payload, tuple(events)
            time.sleep(self.config.poll_interval_seconds)

        raise AdapterError(f"Timed out waiting for run {run_id} to finish.")

    def submit_user_message(self, message: str) -> AdapterTurnResult:
        user_message = message
        self.turn_counter += 1
        effective_message = self._message_with_seed(user_message)
        client_request_id = f"{self.benchmark_run_id}-{self.subject.subject_name}-{self.turn_counter}-{uuid4().hex[:12]}"[:120]
        run_request = {
            "content": effective_message,
            "magi": self.default_mode,
            "client_request_id": client_request_id,
        }
        created_run = self._request_json("POST", f"/chats/{self.chat_id}/runs", payload=run_request)
        run_id = str(created_run.get("id", "")).strip()
        if not run_id:
            raise AdapterError("Run creation did not return an id.")
        self.latest_run_id = run_id

        run_snapshot, events = self._wait_for_terminal_run(run_id)
        status = str(run_snapshot.get("status", "")).strip() or "failed"
        done_event = next((event for event in reversed(events) if event.event_type == RunEventType.DONE), None)
        terminal_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type in {RunEventType.DONE, RunEventType.ERROR, RunEventType.CANCELLED}
            ),
            None,
        )

        assistant_message = ""
        if done_event is not None:
            assistant_message = _extract_message_text(done_event.payload.get("assistant_message", {}) or {})

        return AdapterTurnResult(
            user_message=user_message,
            assistant_message=assistant_message,
            run_id=run_id,
            status=status,
            terminal_event_type=terminal_event.event_type.value if terminal_event else "",
            events=events,
            debug={
                "project_id": self.project_id,
                "chat_id": self.chat_id,
                "request_payload": run_request,
                "run_snapshot": run_snapshot,
                "seed_strategy": self.seed_strategy,
            },
            metadata={
                "magi_mode": self.default_mode,
                "subject_name": self.subject.subject_name,
            },
        )

    def _cancel_active_run_if_needed(self) -> dict[str, Any]:
        if not self.latest_run_id:
            return {"latest_run_id": "", "cancel_attempted": False, "cancelled_active_run": False}
        try:
            run_snapshot = self._request_json("GET", f"/runs/{self.latest_run_id}")
        except Exception as exc:
            return {
                "latest_run_id": self.latest_run_id,
                "cancel_attempted": False,
                "cancelled_active_run": False,
                "cancel_error": str(exc),
            }
        status = str(run_snapshot.get("status", "")).strip()
        if status in TERMINAL_RUN_STATUSES:
            return {
                "latest_run_id": self.latest_run_id,
                "latest_run_status": status,
                "cancel_attempted": False,
                "cancelled_active_run": False,
            }
        try:
            cancel_payload = self._request_json("POST", f"/runs/{self.latest_run_id}/cancel", payload={})
        except Exception as exc:
            return {
                "latest_run_id": self.latest_run_id,
                "latest_run_status": status,
                "cancel_attempted": True,
                "cancelled_active_run": False,
                "cancel_error": str(exc),
            }
        cancel_status = ""
        if isinstance(cancel_payload, dict):
            cancel_status = str(cancel_payload.get("status", "")).strip()
        return {
            "latest_run_id": self.latest_run_id,
            "latest_run_status": status,
            "cancel_attempted": True,
            "cancelled_active_run": True,
            "cancel_response_status": cancel_status,
        }

    def close(self) -> dict[str, Any]:
        self.client.close()
        return {
            "project_id": self.project_id,
            "chat_id": self.chat_id,
            "latest_run_id": self.latest_run_id,
        }

    def abort(self) -> dict[str, Any]:
        metadata = {
            "project_id": self.project_id,
            "chat_id": self.chat_id,
            **self._cancel_active_run_if_needed(),
        }
        self.client.close()
        return metadata


class AILinuxAssistantHttpAdapter(SubjectAdapter):
    name = "ai_linux_assistant_http"

    def __init__(self, config: AILinuxAssistantHttpConfig):
        self.config = config

    def create_session(self, benchmark_run_id: str, subject: SubjectSpec) -> SubjectSession:
        return AILinuxAssistantHttpSession(
            client=requests.Session(),
            config=self.config,
            benchmark_run_id=benchmark_run_id,
            subject=subject,
        )


AILinuxAssistantHTTPConfig = AILinuxAssistantHttpConfig
AILinuxAssistantHTTPAdapter = AILinuxAssistantHttpAdapter
