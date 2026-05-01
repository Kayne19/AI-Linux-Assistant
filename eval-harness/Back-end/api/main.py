"""FastAPI factory for the eval harness control center."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import get_store
from .routes import (
    admin,
    artifacts,
    data,
    generate,
    infra,
    judge,
    runs,
    scenarios,
    subjects,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    store = get_store()
    # Crash recovery: mark orphaned pending/running rows as failed
    # (we do this via raw SQL since store methods don't expose bulk updates)
    from eval_harness.persistence.postgres_models import (
        BenchmarkRunRecord,
        EvaluationRunRecord,
        ScenarioSetupRunRecord,
    )
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    session_factory = store._session_factory
    try:
        with session_factory() as session:
            session.execute(
                update(ScenarioSetupRunRecord)
                .where(ScenarioSetupRunRecord.status.in_(["pending", "running"]))
                .values(status="failed", failure_reason="interrupted_by_restart")
            )
            session.execute(
                update(BenchmarkRunRecord)
                .where(BenchmarkRunRecord.status.in_(["pending", "running"]))
                .values(status="interrupted")
            )
            session.execute(
                update(EvaluationRunRecord)
                .where(EvaluationRunRecord.status.in_(["pending", "running"]))
                .values(status="interrupted")
            )
            session.commit()
    except OperationalError:
        import logging

        logging.getLogger("eval-harness-api").warning(
            "Postgres not reachable at startup; skipping crash-recovery cleanup."
        )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Eval Harness Control Center",
        version="0.1.0",
        lifespan=_lifespan,
    )

    origins = os.getenv("EVAL_HARNESS_CORS_ORIGINS", "http://localhost:5174").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(scenarios.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(infra.router, prefix="/api/v1")
    app.include_router(generate.router, prefix="/api/v1")
    app.include_router(subjects.router, prefix="/api/v1")
    app.include_router(judge.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(data.router, prefix="/api/v1")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    return app
