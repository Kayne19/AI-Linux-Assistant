"""Scenario generation routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import PlannerDep
from ..schemas import (
    GenerateResponse,
    ScenarioGenerateRequest,
    ScenarioValidateRequest,
    ValidateResponse,
)

router = APIRouter(tags=["generate"])


@router.post("/scenarios/generate")
def generate_scenario(
    body: ScenarioGenerateRequest, planner: PlannerDep
) -> GenerateResponse:
    """Generate a scenario spec from a planning brief using the configured planner."""
    from eval_harness.models import PlannerScenarioRequest

    request = PlannerScenarioRequest(
        planning_brief=body.planning_brief,
        target_image=body.target_image or "",
        scenario_name_hint=body.scenario_name_hint or "",
        constraints=tuple(body.constraints or ()),
        tags=tuple(body.tags or ()),
    )
    spec = planner.generate_scenario(request)  # type: ignore[attr-defined]
    # Allow possibly-invalid scenarios through; frontend can call /scenarios/validate
    return GenerateResponse(scenario=spec.to_dict())


@router.post("/scenarios/validate")
def validate_scenario_endpoint(body: ScenarioValidateRequest) -> ValidateResponse:
    """Validate a scenario JSON against the schema constraints."""
    from eval_harness.models import ScenarioSpec
    from eval_harness.scenario import ScenarioValidationError, validate_scenario

    try:
        spec = ScenarioSpec.from_dict(body.scenario_json)
        validate_scenario(spec)
        return ValidateResponse(valid=True, errors=[])
    except ScenarioValidationError as exc:
        return ValidateResponse(valid=False, errors=str(exc).split("; "))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
