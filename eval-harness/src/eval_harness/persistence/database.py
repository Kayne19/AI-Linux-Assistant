from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Dedicated declarative base for eval-harness persistence models."""


def normalize_database_url(database_url: str) -> str:
    normalized = str(database_url or "").strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://") :]
    parsed = urlparse(normalized)
    if parsed.query:
        cleaned_query = urlencode(
            [(key.strip(), value.strip()) for key, value in parse_qsl(parsed.query, keep_blank_values=True)],
            doseq=True,
        )
        normalized = urlunparse(parsed._replace(query=cleaned_query))
    return normalized


def get_database_url() -> str:
    """Resolve eval-harness DB URL with explicit override precedence."""
    url = os.getenv("EVAL_HARNESS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Missing database URL. Set EVAL_HARNESS_DATABASE_URL or DATABASE_URL.")
    return normalize_database_url(url)


def build_engine(database_url: str | None = None, *, echo: bool = False) -> Engine:
    resolved = normalize_database_url(database_url or get_database_url())
    if resolved.startswith("sqlite") and ":memory:" in resolved:
        return create_engine(
            resolved,
            future=True,
            pool_pre_ping=True,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(resolved, future=True, pool_pre_ping=True, echo=echo)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


def create_all_tables(engine: Engine) -> None:
    from . import postgres_models  # noqa: F401

    Base.metadata.create_all(engine)
    _apply_eval_harness_migrations(engine)


def drop_all_tables(engine: Engine) -> None:
    from . import postgres_models  # noqa: F401

    Base.metadata.drop_all(engine)


def _apply_eval_harness_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("scenario_revisions")}
    if "initial_user_message" in columns:
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE scenario_revisions ADD COLUMN initial_user_message TEXT NOT NULL DEFAULT ''"
        )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
