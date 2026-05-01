"""FastAPI factory for the eval harness control center."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError

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


def _redact_database_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.username is None and parsed.password is None:
        return raw
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    userinfo = parsed.username or ""
    if parsed.password is not None:
        userinfo = f"{userinfo}:***"
    return urlunsplit(
        (parsed.scheme, f"{userinfo}@{hostname}{port}", parsed.path, parsed.query, parsed.fragment)
    )


def _database_error_response(message: str, status_code: int = 503) -> JSONResponse:
    configured_url = os.getenv("EVAL_HARNESS_DATABASE_URL") or os.getenv("DATABASE_URL")
    detail = {
        "error": "eval_harness_database_unavailable",
        "message": message,
        "configured_database_url": _redact_database_url(configured_url),
        "hint": (
            "Set EVAL_HARNESS_DATABASE_URL in eval-harness/.env, ensure the "
            "database server is running, then call POST /api/v1/admin/init-db."
        ),
    }
    return JSONResponse(status_code=status_code, content=detail)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Crash recovery: mark orphaned pending/running rows as failed
    # (we do this via raw SQL since store methods don't expose bulk updates)
    from eval_harness.persistence.postgres_models import (
        BenchmarkRunRecord,
        EvaluationRunRecord,
        ScenarioSetupRunRecord,
    )
    from sqlalchemy import update

    try:
        store = get_store()
        session_factory = store._session_factory
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
    except (OperationalError, RuntimeError):
        import logging

        logging.getLogger("eval-harness-api").warning(
            "Eval harness database not reachable at startup; skipping crash-recovery cleanup."
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

    @app.exception_handler(OperationalError)
    async def database_operational_error_handler(
        _request: Request,
        exc: OperationalError,
    ) -> JSONResponse:
        return _database_error_response(str(exc.orig or exc))

    @app.exception_handler(RuntimeError)
    async def runtime_error_handler(
        _request: Request,
        exc: RuntimeError,
    ) -> JSONResponse:
        if "Missing database URL" in str(exc):
            return _database_error_response(str(exc), status_code=503)
        raise exc

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
