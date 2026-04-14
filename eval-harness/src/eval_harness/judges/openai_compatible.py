from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from .base import BlindJudge
from ..models import BlindJudgeRequest, BlindJudgeResult


def _extract_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return str(content)


def _extract_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"Expected JSON object in judge response, got: {text[:200]!r}")
    return json.loads(text[start:end + 1])


@dataclass(frozen=True)
class OpenAICompatibleBlindJudgeConfig:
    base_url: str
    model: str
    api_key: str
    request_timeout_seconds: float = 60.0


class OpenAICompatibleBlindJudge(BlindJudge):
    name = "openai_compatible_blind_judge"

    def __init__(self, config: OpenAICompatibleBlindJudgeConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }
        )

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        response = self.session.post(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            json={
                "model": self.config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a blind benchmark judge. Grade the transcript against the rubric without inferring system identity. "
                            "Return JSON only with blind_label, summary, and scores."
                        ),
                    },
                    {"role": "user", "content": json.dumps(request.to_dict(), indent=2)},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=self.config.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = _extract_json_object(_extract_text(response.json()))
        if "blind_label" not in payload:
            payload["blind_label"] = request.blind_label
        if "raw_response" not in payload:
            payload["raw_response"] = payload
        return BlindJudgeResult.from_dict(payload)
