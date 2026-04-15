from __future__ import annotations

from eval_harness.adapters.ai_linux_assistant_http import AILinuxAssistantHttpConfig, AILinuxAssistantHttpSession
from eval_harness.models import RunEvent, RunEventType, SubjectSpec, TurnSeed


class FakeAILinuxAssistantHttpSession(AILinuxAssistantHttpSession):
    def __init__(self, *, config: AILinuxAssistantHttpConfig, benchmark_run_id: str, subject: SubjectSpec):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.run_status = "completed"
        fake_client = type("FakeClient", (), {"close": lambda self: None})()
        super().__init__(
            client=fake_client,  # type: ignore[arg-type]
            config=config,
            benchmark_run_id=benchmark_run_id,
            subject=subject,
        )

    def _request_json(self, method: str, path: str, *, payload: dict | None = None):
        self.requests.append((method.upper(), path, dict(payload) if payload is not None else None))
        if method.upper() == "POST" and path == "/projects":
            return {"id": "project-1"}
        if method.upper() == "POST" and path == "/projects/project-1/chats":
            return {"id": "chat-1"}
        if method.upper() == "POST" and path == "/chats/chat-1/runs":
            return {"id": "run-1"}
        if method.upper() == "GET" and path == "/runs/run-1":
            return {"id": "run-1", "status": self.run_status}
        if method.upper() == "GET" and path.startswith("/runs/run-1/events"):
            return []
        return {}

    def _wait_for_terminal_run(self, run_id: str):
        return (
            {"id": run_id, "status": "completed"},
            (
                RunEvent(
                    seq=1,
                    event_type=RunEventType.DONE,
                    code="done",
                    payload={"assistant_message": {"content": "fixed"}},
                ),
            ),
        )


def test_ai_linux_assistant_http_session_keeps_only_context_seed_and_user_request() -> None:
    config = AILinuxAssistantHttpConfig(base_url="https://example.invalid")
    subject = SubjectSpec(
        subject_name="magi-full",
        adapter_type="ai_linux_assistant_http",
        adapter_config={"magi_mode": "full"},
    )
    session = FakeAILinuxAssistantHttpSession(
        config=config,
        benchmark_run_id="bench-1",
        subject=subject,
    )
    session.seed_context((TurnSeed(role="system", content="Previous benchmark context."),))

    result = session.submit_user_message("Restore nginx and bring localhost back.")

    run_request = next(
        payload
        for method, path, payload in session.requests
        if method == "POST" and path == "/chats/chat-1/runs"
    )

    assert result.assistant_message == "fixed"
    assert run_request is not None
    assert run_request["magi"] == "full"
    assert "automated troubleshooting benchmark" not in run_request["content"]
    assert "All commands needed to solve the benchmark are pre-approved." not in run_request["content"]
    assert "Previous benchmark context." in run_request["content"]
    assert "Current user request:\n\nRestore nginx and bring localhost back." in run_request["content"]


def test_ai_linux_assistant_http_session_abort_cancels_active_run() -> None:
    config = AILinuxAssistantHttpConfig(base_url="https://example.invalid")
    subject = SubjectSpec(
        subject_name="regular",
        adapter_type="ai_linux_assistant_http",
    )
    session = FakeAILinuxAssistantHttpSession(
        config=config,
        benchmark_run_id="bench-1",
        subject=subject,
    )
    session.latest_run_id = "run-1"
    session.run_status = "running"

    metadata = session.abort()

    assert metadata["latest_run_id"] == "run-1"
    assert metadata["cancel_attempted"] is True
    assert metadata["cancelled_active_run"] is True
    assert ("POST", "/runs/run-1/cancel", {}) in session.requests
