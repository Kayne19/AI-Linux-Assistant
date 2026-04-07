"""Tests for GET /admin/settings and PUT /admin/settings.

Uses an in-memory SQLite database and a fake auth verifier so no real
network calls or Postgres connections are needed.
"""

import os
import tempfile
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import AuthVerificationError, build_current_user_dependency
from persistence.database import Base
import persistence.postgres_models  # noqa: F401 — registers all models with Base
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_run_store import PostgresRunStore
from persistence.postgres_models import User


class _FakeVerifier:
    def __init__(self, claims_by_token):
        self._claims = claims_by_token

    def verify_access_token(self, token):
        if token not in self._claims:
            raise AuthVerificationError("Invalid token.")
        return dict(self._claims[token])


def _build_test_app():
    """Build a FastAPI TestClient backed by SQLite with an admin and a regular user."""
    from fastapi.testclient import TestClient
    from api import create_app

    fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    app_store = PostgresAppStore(session_factory=session_factory)
    run_store = PostgresRunStore(session_factory=session_factory)

    # Seed admin and regular users
    admin_user = app_store.find_or_create_auth_user(
        auth_provider="auth0", auth_subject="auth0|admin",
        email="admin@example.com", email_verified=True, display_name="Admin",
    )
    regular_user = app_store.find_or_create_auth_user(
        auth_provider="auth0", auth_subject="auth0|user",
        email="user@example.com", email_verified=True, display_name="User",
    )
    with session_factory() as session:
        stored_admin = session.get(User, admin_user.id)
        stored_admin.role = "admin"
        session.commit()

    verifier = _FakeVerifier({
        "admin-token": {"sub": "auth0|admin", "email": "admin@example.com",
                        "email_verified": True, "name": "Admin", "picture": ""},
        "user-token": {"sub": "auth0|user", "email": "user@example.com",
                       "email_verified": True, "name": "User", "picture": ""},
    })

    app = create_app(app_store=app_store, run_store=run_store, auth_verifier=verifier)

    # Patch get_session_factory so the admin routes use our SQLite session factory
    import persistence.database as db_mod
    original = getattr(db_mod, "_test_session_factory_override", None)
    db_mod._test_session_factory_override = session_factory
    # Monkey-patch the function used inside the route handlers
    original_gsf = db_mod.get_session_factory
    db_mod.get_session_factory = lambda: session_factory

    client = TestClient(app, raise_server_exceptions=True)
    return client, session_factory, db_mod, original_gsf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_get_settings_non_admin_returns_403():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.get("/admin/settings", headers={"Authorization": "Bearer user-token"})
        assert response.status_code == 403
    finally:
        db_mod.get_session_factory = original_gsf


def test_get_settings_returns_all_17_components():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.get("/admin/settings", headers={"Authorization": "Bearer admin-token"})
        assert response.status_code == 200
        data = response.json()
        expected_keys = [
            "classifier", "contextualizer", "responder",
            "magi_eager", "magi_skeptic", "magi_historian", "magi_arbiter",
            "magi_lite_eager", "magi_lite_skeptic", "magi_lite_historian", "magi_lite_arbiter",
            "history_summarizer", "context_summarizer", "memory_extractor",
            "registry_updater", "ingest_enricher", "chat_namer",
        ]
        for key in expected_keys:
            assert key in data, f"Missing component: {key}"
            comp = data[key]
            assert "provider" in comp
            assert "model" in comp
            assert "reasoning_effort" in comp
            assert "is_default" in comp
    finally:
        db_mod.get_session_factory = original_gsf


def test_get_settings_is_default_true_when_no_overrides():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.get("/admin/settings", headers={"Authorization": "Bearer admin-token"})
        assert response.status_code == 200
        data = response.json()
        for key, comp in data.items():
            assert comp["is_default"] is True, f"{key}.is_default should be True with no DB overrides"
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_non_admin_returns_403():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.put(
            "/admin/settings",
            json={"responder": {"provider": "anthropic", "model": "claude-opus-4-6", "reasoning_effort": "high"}},
            headers={"Authorization": "Bearer user-token"},
        )
        assert response.status_code == 403
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_invalid_provider_returns_422():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.put(
            "/admin/settings",
            json={"responder": {"provider": "invalid-provider", "model": "gpt-5.4", "reasoning_effort": "low"}},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert response.status_code == 422
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_invalid_reasoning_effort_returns_422():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        response = client.put(
            "/admin/settings",
            json={"responder": {"provider": "openai", "model": "gpt-5.4", "reasoning_effort": "ultra"}},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert response.status_code == 422
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_partial_patch_updates_only_specified_fields():
    client, session_factory, db_mod, original_gsf = _build_test_app()
    try:
        # Patch only the responder
        put_response = client.put(
            "/admin/settings",
            json={"responder": {"provider": "anthropic", "model": "claude-opus-4-6", "reasoning_effort": "high"}},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert put_response.status_code == 200
        data = put_response.json()

        # Responder should reflect the update and is_default should be False
        assert data["responder"]["provider"] == "anthropic"
        assert data["responder"]["model"] == "claude-opus-4-6"
        assert data["responder"]["reasoning_effort"] == "high"
        assert data["responder"]["is_default"] is False

        # Other components should remain default
        assert data["classifier"]["is_default"] is True
        assert data["magi_eager"]["is_default"] is True

        # Subsequent GET should reflect the same state
        get_response = client.get("/admin/settings", headers={"Authorization": "Bearer admin-token"})
        assert get_response.status_code == 200
        get_data = get_response.json()
        assert get_data["responder"]["provider"] == "anthropic"
        assert get_data["classifier"]["is_default"] is True
    finally:
        db_mod.get_session_factory = original_gsf
