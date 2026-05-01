from __future__ import annotations

from abc import ABC, abstractmethod

from ..backends.base import SandboxHandle
from ..models import CommandExecutionResult


class InteractiveSession(ABC):
    """Persistent terminal session."""
    
    @abstractmethod
    def send_input(self, input_text: str = "", control_keys: tuple[str, ...] = ()) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_output(self, timeout_seconds: float = 5.0) -> str:
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class SandboxController(ABC):
    """Controller owns sandbox interaction transport only."""

    name: str

    @abstractmethod
    def execute_commands(
        self,
        commands: tuple[str, ...],
        *,
        agent_id: str = "",
        session_key: str | None = None,
    ) -> tuple[CommandExecutionResult, ...]:
        raise NotImplementedError

    def execute_command(
        self,
        command: str,
        *,
        agent_id: str = "",
        session_key: str | None = None,
    ) -> CommandExecutionResult:
        results = self.execute_commands((command,), agent_id=agent_id, session_key=session_key)
        return results[0]

    def open_session(self, session_key: str) -> InteractiveSession:
        raise NotImplementedError(f"{self.__class__.__name__} does not support interactive sessions.")

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class SandboxControllerFactory(ABC):
    """Factory for controller instances bound to a specific sandbox handle."""

    @abstractmethod
    def open(self, handle: SandboxHandle, *, purpose: str = "") -> SandboxController:
        raise NotImplementedError
