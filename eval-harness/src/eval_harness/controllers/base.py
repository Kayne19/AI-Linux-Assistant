from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import VerificationCheck, VerificationResult


class SandboxController(ABC):
    """Controller owns sandbox interaction transport only."""

    name: str

    @abstractmethod
    def send(self, *, agent_id: str, message: str, session_key: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def run_verification(self, check: VerificationCheck, *, agent_id: str, session_key: str | None = None) -> VerificationResult:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
