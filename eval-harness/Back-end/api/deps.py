"""FastAPI dependency injection for the eval harness API."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from eval_harness.persistence.store import EvalHarnessStore


@lru_cache
def _get_db_url() -> str:
    return os.getenv("EVAL_HARNESS_DATABASE_URL", "postgresql://localhost/eval_harness")


@lru_cache
def _get_session_factory():
    engine = create_engine(_get_db_url(), pool_pre_ping=True)
    return sessionmaker(bind=engine)


@lru_cache
def get_store() -> EvalHarnessStore:
    return EvalHarnessStore(_get_session_factory())


StoreDep = Annotated[EvalHarnessStore, Depends(get_store)]


@lru_cache
def get_planner():
    """Create a ScenarioPlanner from environment variables.

    Reads EVAL_HARNESS_PLANNER_PROVIDER (openai|anthropic|google, default openai),
    EVAL_HARNESS_PLANNER_MODEL, EVAL_HARNESS_PLANNER_API_KEY,
    and EVAL_HARNESS_PLANNER_BASE_URL.
    """
    provider = os.getenv("EVAL_HARNESS_PLANNER_PROVIDER", "openai").strip().lower()
    model = os.getenv("EVAL_HARNESS_PLANNER_MODEL", "gpt-5.1").strip()
    api_key = os.getenv("EVAL_HARNESS_PLANNER_API_KEY") or os.getenv(
        "OPENAI_API_KEY", ""
    )
    base_url = os.getenv("EVAL_HARNESS_PLANNER_BASE_URL") or None

    if provider == "openai":
        from eval_harness.planners.openai_responses import (
            OpenAIResponsesScenarioPlanner,
            OpenAIResponsesScenarioPlannerConfig,
        )

        return OpenAIResponsesScenarioPlanner(
            OpenAIResponsesScenarioPlannerConfig(
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        )
    if provider == "anthropic":
        from eval_harness.planners.anthropic import (
            AnthropicScenarioPlanner,
            AnthropicScenarioPlannerConfig,
        )

        return AnthropicScenarioPlanner(
            AnthropicScenarioPlannerConfig(
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        )
    if provider == "google":
        from eval_harness.planners.google_genai import (
            GoogleGenAIScenarioPlanner,
            GoogleGenAIScenarioPlannerConfig,
        )

        return GoogleGenAIScenarioPlanner(
            GoogleGenAIScenarioPlannerConfig(
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        )
    raise ValueError(f"Unsupported planner provider: {provider}")


PlannerDep = Annotated[object, Depends(get_planner)]
