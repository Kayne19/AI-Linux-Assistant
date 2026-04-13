from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class ProviderStepResult:
    output_text: str = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    session_state: Any = None
