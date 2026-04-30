import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from auth import AuthVerificationError, build_current_user_dependency
from persistence.database import Base
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_models import User
from persistence.postgres_run_store import PostgresRunStore


def _build_stores():
    fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return (
        PostgresAppStore(session_factory=session_factory),
        PostgresRunStore(session_factory=session_factory),
        session_factory,
    )


class _FakeVerifier:
    def __init__(self, claims_by_token):
        self._claims_by_token = claims_by_token

    def verify_access_token(self, token):
        if token not in self._claims_by_token:
            raise AuthVerificationError("Invalid bearer token.")
        return dict(self._claims_by_token[token])


def _seed_owned_state(app_store, run_store, session_factory):
    owner = app_store.find_or_create_auth_user(
        auth_provider="auth0",
        auth_subject="auth0|owner",
        email="owner@example.com",
        email_verified=True,
        display_name="Owner",
    )
    other = app_store.find_or_create_auth_user(
        auth_provider="auth0",
        auth_subject="auth0|other",
        email="other@example.com",
        email_verified=True,
        display_name="Other",
    )
    admin = app_store.find_or_create_auth_user(
        auth_provider="auth0",
        auth_subject="auth0|admin",
        email="admin@example.com",
        email_verified=True,
        display_name="Admin",
    )
    with session_factory() as session:
        stored_admin = session.get(User, admin.id)
        stored_admin.role = "admin"
        session.commit()

    owner_project = app_store.create_project(owner.id, "Owner Project")
    owner_chat = app_store.create_chat_session(owner_project.id, title="Owner Chat")
    app_store.append_message_for_user(owner_chat.id, owner.id, "user", "hello")
    owner_run = run_store.create_or_reuse_run(
        chat_session_id=owner_chat.id,
        project_id=owner_project.id,
        user_id=owner.id,
        request_content="owner request",
        magi="off",
        client_request_id="owner-run",
        max_active_runs_per_user=3,
    )

    other_project = app_store.create_project(other.id, "Other Project")
    other_chat = app_store.create_chat_session(other_project.id, title="Other Chat")
    other_run = run_store.create_or_reuse_run(
        chat_session_id=other_chat.id,
        project_id=other_project.id,
        user_id=other.id,
        request_content="other request",
        magi="off",
        client_request_id="other-run",
        max_active_runs_per_user=3,
    )

    return (
        owner,
        other,
        admin,
        owner_project,
        owner_chat,
        owner_run,
        other_project,
        other_chat,
        other_run,
    )


def test_current_user_dependency_creates_and_syncs_local_auth0_user():
    app_store, _run_store, _session_factory = _build_stores()
    dependency = build_current_user_dependency(
        app_store,
        _FakeVerifier(
            {
                "owner-token": {
                    "sub": "auth0|owner",
                    "email": "owner@example.com",
                    "email_verified": True,
                    "name": "Owner",
                    "picture": "https://example.com/owner.png",
                }
            }
        ),
    )

    current_user = dependency("Bearer owner-token")

    assert current_user.auth_provider == "auth0"
    assert current_user.auth_subject == "auth0|owner"
    assert current_user.email == "owner@example.com"
    assert current_user.email_verified is True
    assert current_user.display_name == "Owner"
    assert current_user.avatar_url == "https://example.com/owner.png"


def test_bootstrap_session_and_owner_scoped_reads_exclude_other_users():
    app_store, run_store, session_factory = _build_stores()
    (
        owner,
        other,
        _admin,
        owner_project,
        owner_chat,
        owner_run,
        other_project,
        other_chat,
        other_run,
    ) = _seed_owned_state(
        app_store,
        run_store,
        session_factory,
    )

    bootstrap = app_store.bootstrap_app_session(owner.id)

    assert bootstrap is not None
    assert bootstrap["user"].id == owner.id
    assert [project["id"] for project in bootstrap["projects"]] == [owner_project.id]
    assert owner_project.id in bootstrap["chats_by_project"]
    assert bootstrap["chats_by_project"][owner_project.id][0]["id"] == owner_chat.id
    assert other_project.id not in bootstrap["chats_by_project"]

    assert app_store.get_project_for_user(owner_project.id, owner.id) is not None
    assert app_store.get_project_for_user(other_project.id, owner.id) is None
    assert app_store.get_chat_session_for_user(owner_chat.id, owner.id) is not None
    assert app_store.get_chat_session_for_user(other_chat.id, owner.id) is None
    assert app_store.list_messages_for_user_chat(owner.id, owner_chat.id) is not None
    assert app_store.list_messages_for_user_chat(owner.id, other_chat.id) is None
    assert run_store.get_run_for_user(owner_run.id, owner.id) is not None
    assert run_store.get_run_for_user(other_run.id, owner.id) is None
    assert (
        run_store.list_runs_for_chat_for_user(
            owner_chat.id, owner.id, page=1, page_size=10
        )[1]
        == 1
    )

    assert app_store.get_project_for_user(owner_project.id, other.id) is None
    assert run_store.get_run_for_user(owner_run.id, other.id) is None


def test_local_role_remains_authorization_source():
    app_store, _run_store, session_factory = _build_stores()
    (
        _owner,
        _other,
        admin,
        _owner_project,
        _owner_chat,
        _owner_run,
        _other_project,
        _other_chat,
        _other_run,
    ) = _seed_owned_state(
        app_store,
        PostgresRunStore(session_factory=session_factory),
        session_factory,
    )
    with session_factory() as session:
        stored_admin = session.get(User, admin.id)
        assert stored_admin.role == "admin"
