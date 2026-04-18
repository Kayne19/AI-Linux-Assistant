from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.judges.openai_responses import (
    OpenAIResponsesBlindJudge,
    OpenAIResponsesBlindJudgeConfig,
    _blind_judge_schema,
)
from eval_harness.models import BlindJudgeRequest, TurnRecord


class FakeJudge(OpenAIResponsesBlindJudge):
    def __init__(self, responses: list[dict]):
        super().__init__(OpenAIResponsesBlindJudgeConfig(model="gpt-5.4-mini", api_key="test-key"))
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def _request_json_schema(
        self,
        *,
        instructions: str,
        user_input: str,
        schema_name: str,
        schema: dict,
        schema_description: str = "",
    ) -> dict:
        self.calls.append(
            {
                "instructions": instructions,
                "user_input": user_input,
                "schema_name": schema_name,
                "schema": schema,
                "schema_description": schema_description,
            }
        )
        return self.responses.pop(0)


def test_blind_judge_uses_structured_output_and_backfills_defaults() -> None:
    judge = FakeJudge(
        [
            {
                "summary": "The assistant converged on the right fix after checking systemd logs.",
                "scores": {"diagnosis": 4, "efficiency": 3},
            }
        ]
    )
    request = BlindJudgeRequest(
        blind_label="candidate-1",
        transcript=(
            TurnRecord(role="user", content="nginx is down"),
            TurnRecord(role="assistant", content="Please run `systemctl status nginx`."),
        ),
        rubric=("Correct diagnosis", "Efficient troubleshooting"),
        repair_success=True,
    )

    result = judge.grade(request)

    assert judge.calls[0]["schema_name"] == "blind_judge_result"
    assert result.blind_label == "candidate-1"
    assert result.summary.startswith("The assistant converged")
    assert result.scores == {"diagnosis": 4, "efficiency": 3}
    assert result.raw_response["scores"]["diagnosis"] == 4


def test_blind_judge_normalizes_wire_score_items_to_persisted_score_dict() -> None:
    judge = FakeJudge(
        [
            {
                "summary": "The assistant found the service-level cause and gave a focused repair path.",
                "scores": [
                    {"criterion": "Correct diagnosis", "score": 4},
                    {"criterion": "Efficient troubleshooting", "score": 3},
                ],
            }
        ]
    )
    request = BlindJudgeRequest(
        blind_label="candidate-1",
        transcript=(
            TurnRecord(role="user", content="nginx is down"),
            TurnRecord(role="assistant", content="Please run `systemctl status nginx`."),
        ),
        rubric=("Correct diagnosis", "Efficient troubleshooting"),
        repair_success=True,
    )

    result = judge.grade(request)

    assert result.scores == {
        "Correct diagnosis": 4,
        "Efficient troubleshooting": 3,
    }
    assert result.raw_response["scores"] == {
        "Correct diagnosis": 4,
        "Efficient troubleshooting": 3,
    }


def test_blind_judge_schema_is_openai_strict_compatible() -> None:
    def assert_strict_schema(schema: object, *, path: str = "schema") -> None:
        if not isinstance(schema, dict):
            return

        schema_type = schema.get("type")
        if schema_type == "object":
            assert schema.get("additionalProperties") is False, path
            properties = schema.get("properties") or {}
            required = schema.get("required") or []
            assert sorted(required) == sorted(properties.keys()), path
            for key, value in properties.items():
                assert_strict_schema(value, path=f"{path}.properties.{key}")
        elif schema_type == "array":
            assert_strict_schema(schema.get("items"), path=f"{path}.items")

        for keyword in ("anyOf", "allOf", "oneOf"):
            for index, variant in enumerate(schema.get(keyword) or []):
                assert_strict_schema(variant, path=f"{path}.{keyword}[{index}]")

    assert_strict_schema(_blind_judge_schema())
