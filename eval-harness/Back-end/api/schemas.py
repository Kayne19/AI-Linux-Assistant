"""Pydantic request/response models for the eval harness API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool = True


class ScenarioListItem(BaseModel):
    id: str
    scenario_name: str
    title: str
    lifecycle_status: str
    verification_status: str
    benchmark_run_count: int
    created_at: datetime
    current_verified_revision_id: str | None = None
    last_verified_at: datetime | None = None


class ScenarioRevisionItem(BaseModel):
    id: str
    revision_number: int
    target_image: str
    summary: str
    created_at: datetime


class ScenarioDetail(BaseModel):
    id: str
    scenario_name: str
    title: str
    lifecycle_status: str
    verification_status: str
    benchmark_run_count: int
    created_at: datetime
    last_verified_at: datetime | None = None
    current_verified_revision_id: str | None = None
    revisions: list[ScenarioRevisionItem]


class ScenarioCreateRequest(BaseModel):
    title: str
    scenario_name_hint: str


class ScenarioRevisionCreateRequest(BaseModel):
    target_image: str
    summary: str
    what_it_tests: dict[str, Any]
    observable_problem_statement: str
    initial_user_message: str = ""
    sabotage_plan: dict[str, Any]
    verification_plan: dict[str, Any]
    judge_rubric: dict[str, Any]
    planner_metadata: dict[str, Any] | None = None


class ScenarioGenerateRequest(BaseModel):
    planning_brief: str
    target_image: str | None = None
    scenario_name_hint: str | None = None
    tags: list[str] | None = None
    constraints: list[str] | None = None


class ScenarioValidateRequest(BaseModel):
    scenario_json: dict[str, Any]


class RunEventItem(BaseModel):
    id: str
    seq: int
    actor_role: str
    event_kind: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class SetupRunEventItem(BaseModel):
    id: str
    round_index: int
    seq: int
    actor_role: str
    event_kind: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class RunListItem(BaseModel):
    id: str
    kind: str  # "setup" | "benchmark"
    scenario_revision_id: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    # setup-run fields
    correction_count: int | None = None
    max_corrections: int | None = None
    staging_handle_id: str | None = None
    broken_image_id: str | None = None
    failure_reason: str | None = None
    # benchmark-run fields
    verified_setup_run_id: str | None = None
    subject_count: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunListResponse(BaseModel):
    items: list[RunListItem]
    total: int
    page: int
    page_size: int


class SetupRunItem(BaseModel):
    id: str
    scenario_revision_id: str
    status: str
    staging_handle_id: str | None = None
    correction_count: int
    max_corrections: int
    broken_image_id: str | None = None
    failure_reason: str | None = None
    planner_approved_at: datetime | None = None
    backend_metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BenchmarkRunItem(BaseModel):
    id: str
    scenario_revision_id: str
    verified_setup_run_id: str
    status: str
    subject_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    metadata_json: dict[str, Any] | None = None


class EvaluationRunItem(BaseModel):
    id: str
    benchmark_run_id: str
    subject_id: str
    clone_handle_id: str | None = None
    status: str
    repair_success: bool | None = None
    resolution_result: dict[str, Any] | None = None
    subject_metadata: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JudgeJobItem(BaseModel):
    id: str
    benchmark_run_id: str
    status: str
    judge_adapter_type: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class JudgeJobDetail(BaseModel):
    id: str
    benchmark_run_id: str
    status: str
    judge_adapter_type: str
    rubric: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    judge_items: list[JudgeItemDetail]


class JudgeItemDetail(BaseModel):
    id: str
    evaluation_run_id: str | None = None
    blind_label: str
    parsed_scores: dict[str, Any] | None = None
    summary: str
    kind: str
    judge_name: str | None = None


class SubjectItem(BaseModel):
    id: str
    subject_name: str
    adapter_type: str
    display_name: str
    adapter_config: dict[str, Any] | None = None
    is_active: bool
    created_at: datetime


class SubjectCreateRequest(BaseModel):
    subject_name: str
    adapter_type: str
    display_name: str
    adapter_config: dict[str, Any] | None = None
    is_active: bool = True


class SubjectPatchRequest(BaseModel):
    adapter_type: str | None = None
    display_name: str | None = None
    adapter_config: dict[str, Any] | None = None
    is_active: bool | None = None


class VerifyRequest(BaseModel):
    group_id: str | None = None
    revision_id: str | None = None


class BenchmarkRequest(BaseModel):
    setup_run_id: str
    subject_ids: list[str] | None = None


class JudgeRequest(BaseModel):
    mode: str = "absolute"
    anchor_subject: str | None = None
    bootstrap_samples: int = 200


class RunAllRequest(BaseModel):
    group_id: str | None = None
    revision_id: str | None = None
    subject_ids: list[str] | None = None
    judge_mode: str = "absolute"
    judge_anchor_subject: str | None = None


class InstanceItem(BaseModel):
    instance_id: str
    state: str
    instance_type: str
    public_ip: str | None = None
    tags: dict[str, str]
    launched_at: datetime | None = None


class ImageItem(BaseModel):
    image_id: str
    name: str | None = None
    state: str
    tags: dict[str, str]
    created_at: datetime | None = None


class PreflightResponse(BaseModel):
    ok: bool
    message: str


class GenerateResponse(BaseModel):
    scenario: dict[str, Any]


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = []


class ScenarioCreateResponse(BaseModel):
    id: str
    scenario_name: str
    title: str
    lifecycle_status: str
    verification_status: str
    created_at: datetime


class ScenarioRevisionCreateResponse(BaseModel):
    id: str
    revision_number: int
    target_image: str
    summary: str
    created_at: datetime


class ArtifactExportRequest(BaseModel):
    artifacts_root: str = "artifacts"


class DataTableResponse(BaseModel):
    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int
    page: int
    page_size: int
