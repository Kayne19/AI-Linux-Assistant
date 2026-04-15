from __future__ import annotations

from eval_harness.controllers.openclaw import OpenClawController, OpenClawControllerConfig


def test_command_prompt_forces_non_sandbox_exec_host() -> None:
    controller = OpenClawController(
        OpenClawControllerConfig(
            base_url="http://127.0.0.1:18789",
            token="token-123",
            default_session_key="session-1",
        )
    )
    prompt = controller._command_prompt("systemctl is-active nginx")

    assert "Do not use exec host=sandbox." in prompt
    assert "use host=auto or host=gateway." in prompt

    controller.close()
