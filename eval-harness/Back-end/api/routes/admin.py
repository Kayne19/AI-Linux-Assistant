"""Admin routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["admin"])


@router.post("/admin/init-db")
def init_db() -> dict:
    """Initialise the eval harness database schema.

    Creates all tables via SQLAlchemy Metadata.create_all() and applies
    any outstanding migrations.  Idempotent — safe to call repeatedly.
    """
    from eval_harness.env import autoload_dotenv
    from eval_harness.persistence import build_engine, create_all_tables
    from eval_harness.persistence.database import get_database_url

    autoload_dotenv()
    engine = build_engine(get_database_url())
    create_all_tables(engine)
    engine.dispose()
    return {"ok": True, "message": "Database tables created successfully."}
