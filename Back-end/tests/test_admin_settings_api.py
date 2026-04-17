"""Tests for admin settings route helpers and payload contracts.

Uses an in-memory SQLite database and directly invokes the FastAPI route
closures so the tests stay deterministic without TestClient threading.
"""

import os
import tempfile

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from auth import AuthVerificationError
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

    admin_user = app_store.find_or_create_auth_user(
        auth_provider="auth0",
        auth_subject="auth0|admin",
        email="admin@example.com",
        email_verified=True,
        display_name="Admin",
    )
    regular_user = app_store.find_or_create_auth_user(
        auth_provider="auth0",
        auth_subject="auth0|user",
        email="user@example.com",
        email_verified=True,
        display_name="User",
    )
    with session_factory() as session:
        stored_admin = session.get(User, admin_user.id)
        stored_admin.role = "admin"
        session.commit()
    admin_user.role = "admin"

    verifier = _FakeVerifier(
        {
            "admin-token": {
                "sub": "auth0|admin",
                "email": "admin@example.com",
                "email_verified": True,
                "name": "Admin",
                "picture": "",
            },
            "user-token": {
                "sub": "auth0|user",
                "email": "user@example.com",
                "email_verified": True,
                "name": "User",
                "picture": "",
            },
        }
    )

    app = create_app(app_store=app_store, run_store=run_store, auth_verifier=verifier)

    import persistence.database as db_mod

    original_gsf = db_mod.get_session_factory
    db_mod.get_session_factory = lambda: session_factory

    return app, admin_user, regular_user, db_mod, original_gsf


def _route_for(app, path, method):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"Route not found: {method} {path}")


def _admin_dependency(route):
    return route.dependant.dependencies[0].call


def _dump(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def test_get_settings_non_admin_returns_403():
    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del admin_user
    try:
        route = _route_for(app, "/admin/settings", "GET")
        require_admin = _admin_dependency(route)
        with pytest.raises(HTTPException) as exc_info:
            require_admin(current_user=regular_user)
        assert exc_info.value.status_code == 403
    finally:
        db_mod.get_session_factory = original_gsf


def test_get_settings_returns_components_and_scalar_groups():
    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del regular_user
    try:
        route = _route_for(app, "/admin/settings", "GET")
        response = route.endpoint(_admin=admin_user)
        data = _dump(response)
        expected_keys = [
            "classifier",
            "contextualizer",
            "responder",
            "magi_eager",
            "magi_skeptic",
            "magi_historian",
            "magi_arbiter",
            "magi_lite_eager",
            "magi_lite_skeptic",
            "magi_lite_historian",
            "magi_lite_arbiter",
            "history_summarizer",
            "context_summarizer",
            "memory_extractor",
            "registry_updater",
            "ingest_enricher",
            "chat_namer",
        ]
        for key in expected_keys:
            assert key in data, f"Missing component: {key}"
            comp = data[key]
            assert "provider" in comp
            assert "model" in comp
            assert "reasoning_effort" in comp
            assert "is_default" in comp
        assert "retrieval" in data
        assert "history_context" in data
        assert data["retrieval"]["initial_fetch"]["value"] >= 1
        assert "is_default" in data["retrieval"]["initial_fetch"]
        assert data["history_context"]["max_recent_turns"]["value"] >= 1
        assert "is_default" in data["history_context"]["max_recent_turns"]
    finally:
        db_mod.get_session_factory = original_gsf


def test_get_settings_is_default_true_when_no_overrides():
    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del regular_user
    try:
        route = _route_for(app, "/admin/settings", "GET")
        data = _dump(route.endpoint(_admin=admin_user))
        component_keys = [
            "classifier",
            "contextualizer",
            "responder",
            "magi_eager",
            "magi_skeptic",
            "magi_historian",
            "magi_arbiter",
            "magi_lite_eager",
            "magi_lite_skeptic",
            "magi_lite_historian",
            "magi_lite_arbiter",
            "history_summarizer",
            "context_summarizer",
            "memory_extractor",
            "registry_updater",
            "ingest_enricher",
            "chat_namer",
        ]
        for key in component_keys:
            assert data[key]["is_default"] is True, f"{key}.is_default should be True with no DB overrides"
        for key, comp in data["retrieval"].items():
            assert comp["is_default"] is True, f"retrieval.{key}.is_default should be True with no DB overrides"
        for key, comp in data["history_context"].items():
            assert comp["is_default"] is True, f"history_context.{key}.is_default should be True with no DB overrides"
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_non_admin_returns_403():
    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del admin_user
    try:
        route = _route_for(app, "/admin/settings", "PUT")
        require_admin = _admin_dependency(route)
        with pytest.raises(HTTPException) as exc_info:
            require_admin(current_user=regular_user)
        assert exc_info.value.status_code == 403
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_invalid_provider_returns_validation_error():
    from api import AppSettingsPatch

    with pytest.raises(ValidationError):
        AppSettingsPatch(responder={"provider": "invalid-provider", "model": "gpt-5.4", "reasoning_effort": "low"})


def test_put_settings_invalid_reasoning_effort_returns_validation_error():
    from api import AppSettingsPatch

    with pytest.raises(ValidationError):
        AppSettingsPatch(responder={"provider": "openai", "model": "gpt-5.4", "reasoning_effort": "ultra"})


def test_put_settings_partial_patch_updates_only_specified_fields():
    from api import AppSettingsPatch

    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del regular_user
    try:
        route = _route_for(app, "/admin/settings", "PUT")
        response = route.endpoint(
            patch=AppSettingsPatch(
                responder={"provider": "anthropic", "model": "claude-opus-4-6", "reasoning_effort": "high"}
            ),
            current_user=admin_user,
        )
        data = _dump(response)

        assert data["responder"]["provider"] == "anthropic"
        assert data["responder"]["model"] == "claude-opus-4-6"
        assert data["responder"]["reasoning_effort"] == "high"
        assert data["responder"]["is_default"] is False
        assert data["classifier"]["is_default"] is True
        assert data["magi_eager"]["is_default"] is True

        get_route = _route_for(app, "/admin/settings", "GET")
        get_data = _dump(get_route.endpoint(_admin=admin_user))
        assert get_data["responder"]["provider"] == "anthropic"
        assert get_data["classifier"]["is_default"] is True
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_accepts_google_provider():
    from api import AppSettingsPatch

    patch = AppSettingsPatch(
        responder={"provider": "google", "model": "gemini-2.5-flash", "reasoning_effort": "high"}
    )

    assert patch.responder.provider == "google"
    assert patch.responder.model == "gemini-2.5-flash"
    assert patch.responder.reasoning_effort == "high"


def test_put_settings_updates_retrieval_and_history_context_fields():
    from api import AppSettingsPatch

    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del regular_user
    try:
        route = _route_for(app, "/admin/settings", "PUT")
        response = route.endpoint(
            patch=AppSettingsPatch(
                retrieval={
                    "initial_fetch": 55,
                    "final_top_k": 18,
                    "neighbor_pages": 3,
                    "max_expanded": 60,
                    "source_profile_sample": 7000,
                },
                history_context={
                    "max_recent_turns": 6,
                    "summarize_turn_threshold": 20,
                    "summarize_char_threshold": 4200,
                },
            ),
            current_user=admin_user,
        )
        data = _dump(response)

        assert data["retrieval"]["initial_fetch"] == {"value": 55, "is_default": False}
        assert data["retrieval"]["final_top_k"] == {"value": 18, "is_default": False}
        assert data["retrieval"]["neighbor_pages"] == {"value": 3, "is_default": False}
        assert data["retrieval"]["max_expanded"] == {"value": 60, "is_default": False}
        assert data["retrieval"]["source_profile_sample"] == {"value": 7000, "is_default": False}
        assert data["history_context"]["max_recent_turns"] == {"value": 6, "is_default": False}
        assert data["history_context"]["summarize_turn_threshold"] == {"value": 20, "is_default": False}
        assert data["history_context"]["summarize_char_threshold"] == {"value": 4200, "is_default": False}
        assert data["classifier"]["is_default"] is True

        get_route = _route_for(app, "/admin/settings", "GET")
        get_data = _dump(get_route.endpoint(_admin=admin_user))
        assert get_data["retrieval"]["initial_fetch"]["value"] == 55
        assert get_data["history_context"]["max_recent_turns"]["value"] == 6
    finally:
        db_mod.get_session_factory = original_gsf


def test_put_settings_rejects_invalid_retrieval_relationships():
    from api import AppSettingsPatch

    app, admin_user, regular_user, db_mod, original_gsf = _build_test_app()
    del regular_user
    try:
        route = _route_for(app, "/admin/settings", "PUT")
        with pytest.raises(HTTPException) as exc_info:
            route.endpoint(
                patch=AppSettingsPatch(retrieval={"initial_fetch": 10, "final_top_k": 11}),
                current_user=admin_user,
            )
        assert exc_info.value.status_code == 422
        assert "final_top_k" in str(exc_info.value.detail)

        with pytest.raises(HTTPException) as exc_info:
            route.endpoint(
                patch=AppSettingsPatch(retrieval={"final_top_k": 12, "max_expanded": 11}),
                current_user=admin_user,
            )
        assert exc_info.value.status_code == 422
        assert "max_expanded" in str(exc_info.value.detail)
    finally:
        db_mod.get_session_factory = original_gsf
