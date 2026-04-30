from persistence.database import Base, get_engine, get_session_factory

from utils.time_utils import _utc_now

try:
    from sqlalchemy import inspect, select, text, update
    from sqlalchemy.orm import joinedload, selectinload
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    inspect = None
    select = None
    text = None
    update = None
    joinedload = None
    selectinload = None

from persistence.postgres_models import ChatMessage, ChatSession, Project, User


AUTH_PROVIDER_AUTH0 = "auth0"


class PostgresAppStore:
    def __init__(self, session_factory=None):
        if select is None:
            raise ImportError(
                "SQLAlchemy is required for PostgresAppStore. "
                "Install sqlalchemy and alembic in the AI-Linux-Assistant environment."
            )
        self.session_factory = session_factory or get_session_factory()
        self._ensure_user_schema()

    def _session(self):
        return self.session_factory()

    def _get_bound_engine(self):
        bind = getattr(self.session_factory, "kw", {}).get("bind")
        if bind is not None:
            return bind

        with self.session_factory() as session:
            bind = session.get_bind()
        return bind or get_engine()

    def _ensure_user_schema(self):
        engine = self._get_bound_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[
                User.__table__,
                Project.__table__,
                ChatSession.__table__,
                ChatMessage.__table__,
            ],
            checkfirst=True,
        )
        if inspect is None or text is None:
            return
        inspector = inspect(engine)
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        statements = []
        if "role" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"
            )
        if "auth_provider" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN auth_provider VARCHAR(32) NULL"
            )
        if "auth_subject" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN auth_subject VARCHAR(255) NULL"
            )
        if "email" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN email VARCHAR(255) NOT NULL DEFAULT ''"
            )
        if "email_verified" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT FALSE"
            )
        if "display_name" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN display_name VARCHAR(255) NOT NULL DEFAULT ''"
            )
        if "avatar_url" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN avatar_url TEXT NOT NULL DEFAULT ''"
            )
        if "last_login_at" not in user_columns:
            statements.append(
                "ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP WITH TIME ZONE NULL"
            )
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
            # Postgres-only DDL; harmlessly ignored by sqlite tests.
            if engine.dialect.name.startswith("postgres"):
                connection.exec_driver_sql(
                    "ALTER TABLE users ALTER COLUMN username DROP NOT NULL"
                )
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_provider_subject "
                    "ON users (auth_provider, auth_subject)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_users_auth_provider ON users (auth_provider)"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_users_auth_subject ON users (auth_subject)"
                )

    def get_user_by_username(self, username):
        username = (username or "").strip()
        if not username:
            return None
        with self._session() as session:
            return session.scalar(select(User).where(User.username == username))

    def get_user_by_auth_subject(self, auth_provider, auth_subject):
        auth_provider = (auth_provider or "").strip()
        auth_subject = (auth_subject or "").strip()
        if not auth_provider or not auth_subject:
            return None
        with self._session() as session:
            return session.scalar(
                select(User).where(
                    User.auth_provider == auth_provider,
                    User.auth_subject == auth_subject,
                )
            )

    def get_user(self, user_id):
        with self._session() as session:
            return session.scalar(select(User).where(User.id == user_id))

    def get_user_by_id(self, user_id):
        return self.get_user(user_id)

    def find_or_create_user(self, username):
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        with self._session() as session:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username)
                session.add(user)
                session.commit()
                session.refresh(user)
            return user

    def find_or_create_auth_user(
        self,
        *,
        auth_provider,
        auth_subject,
        email="",
        email_verified=False,
        display_name="",
        avatar_url="",
    ):
        auth_provider = (auth_provider or "").strip()
        auth_subject = (auth_subject or "").strip()
        if not auth_provider or not auth_subject:
            raise ValueError("auth identity is required")
        with self._session() as session:
            user = session.scalar(
                select(User).where(
                    User.auth_provider == auth_provider,
                    User.auth_subject == auth_subject,
                )
            )
            if user is None:
                user = User(
                    username=None,
                    auth_provider=auth_provider,
                    auth_subject=auth_subject,
                    email=(email or "").strip(),
                    email_verified=bool(email_verified),
                    display_name=(display_name or "").strip(),
                    avatar_url=(avatar_url or "").strip(),
                    last_login_at=_utc_now(),
                )
                session.add(user)
            else:
                user.email = (email or "").strip()
                user.email_verified = bool(email_verified)
                user.display_name = (display_name or "").strip()
                user.avatar_url = (avatar_url or "").strip()
                user.last_login_at = _utc_now()
                user.updated_at = _utc_now()
            session.commit()
            session.refresh(user)
            return user

    def list_projects(self, user_id):
        with self._session() as session:
            stmt = (
                select(Project)
                .where(Project.user_id == user_id)
                .order_by(Project.updated_at.desc())
            )
            return list(session.scalars(stmt))

    def list_projects_for_user(self, user_id):
        return self.list_projects(user_id)

    def create_project(self, user_id, name, description=""):
        name = (name or "").strip()
        if not name:
            raise ValueError("project name is required")
        with self._session() as session:
            project = Project(
                user_id=user_id, name=name, description=(description or "").strip()
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

    def update_project(self, project_id, name, description=""):
        name = (name or "").strip()
        description = (description or "").strip()
        if not name:
            raise ValueError("project name is required")
        with self._session() as session:
            project = session.scalar(select(Project).where(Project.id == project_id))
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            project.name = name
            project.description = description
            project.updated_at = _utc_now()
            session.commit()
            session.refresh(project)
            return project

    def get_project(self, project_id, user_id=None):
        with self._session() as session:
            stmt = select(Project).where(Project.id == project_id)
            if user_id is not None:
                stmt = stmt.where(Project.user_id == user_id)
            return session.scalar(stmt)

    def get_project_for_user(self, project_id, user_id):
        return self.get_project(project_id, user_id=user_id)

    def update_project_for_user(self, project_id, user_id, name, description=""):
        with self._session() as session:
            project = session.scalar(
                select(Project).where(
                    Project.id == project_id, Project.user_id == user_id
                )
            )
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            name = (name or "").strip()
            if not name:
                raise ValueError("project name is required")
            project.name = name
            project.description = (description or "").strip()
            project.updated_at = _utc_now()
            session.commit()
            session.refresh(project)
            return project

    def delete_project(self, project_id):
        with self._session() as session:
            project = session.scalar(select(Project).where(Project.id == project_id))
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            session.delete(project)
            session.commit()

    def delete_project_for_user(self, project_id, user_id):
        with self._session() as session:
            project = session.scalar(
                select(Project).where(
                    Project.id == project_id, Project.user_id == user_id
                )
            )
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            session.delete(project)
            session.commit()

    def create_chat_session(self, project_id, title=""):
        with self._session() as session:
            chat_session = ChatSession(
                project_id=project_id, title=(title or "").strip()
            )
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def create_chat_session_for_user(self, project_id, user_id, title=""):
        with self._session() as session:
            project = session.scalar(
                select(Project).where(
                    Project.id == project_id, Project.user_id == user_id
                )
            )
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            chat_session = ChatSession(
                project_id=project_id, title=(title or "").strip()
            )
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def list_chat_sessions(self, project_id, limit=50):
        with self._session() as session:
            stmt = (
                select(ChatSession)
                .where(ChatSession.project_id == project_id)
                .order_by(ChatSession.updated_at.desc())
                .limit(max(1, int(limit)))
            )
            return list(session.scalars(stmt))

    def list_chat_sessions_for_user(self, project_id, user_id, limit=50):
        with self._session() as session:
            if (
                session.scalar(
                    select(Project.id).where(
                        Project.id == project_id, Project.user_id == user_id
                    )
                )
                is None
            ):
                return None
            stmt = (
                select(ChatSession)
                .join(Project, ChatSession.project_id == Project.id)
                .where(ChatSession.project_id == project_id, Project.user_id == user_id)
                .order_by(ChatSession.updated_at.desc())
                .limit(max(1, int(limit)))
            )
            return list(session.scalars(stmt))

    def list_chat_sessions_for_user_project(self, user_id, project_id, limit=50):
        return self.list_chat_sessions_for_user(project_id, user_id, limit=limit)

    def get_chat_session(self, chat_session_id):
        with self._session() as session:
            return session.scalar(
                select(ChatSession).where(ChatSession.id == chat_session_id)
            )

    def get_chat_session_for_user(self, chat_session_id, user_id):
        with self._session() as session:
            return session.scalar(
                select(ChatSession)
                .join(Project, ChatSession.project_id == Project.id)
                .where(ChatSession.id == chat_session_id, Project.user_id == user_id)
            )

    def update_chat_session_title(self, chat_session_id, title):
        title = (title or "").strip()
        if not title:
            raise ValueError("chat title is required")
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession).where(ChatSession.id == chat_session_id)
            )
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            chat_session.title = title
            chat_session.updated_at = _utc_now()
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def update_chat_session_title_for_user(self, chat_session_id, user_id, title):
        title = (title or "").strip()
        if not title:
            raise ValueError("chat title is required")
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession)
                .join(Project, ChatSession.project_id == Project.id)
                .where(ChatSession.id == chat_session_id, Project.user_id == user_id)
            )
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            chat_session.title = title
            chat_session.updated_at = _utc_now()
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def delete_chat_session(self, chat_session_id):
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession).where(ChatSession.id == chat_session_id)
            )
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            session.delete(chat_session)
            session.commit()

    def delete_chat_session_for_user(self, chat_session_id, user_id):
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession)
                .join(Project, ChatSession.project_id == Project.id)
                .where(ChatSession.id == chat_session_id, Project.user_id == user_id)
            )
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            session.delete(chat_session)
            session.commit()

    def get_session_context(self, chat_session_id):
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession)
                .where(ChatSession.id == chat_session_id)
                .options(joinedload(ChatSession.project))
            )
            if chat_session is None or chat_session.project is None:
                return None
            return {
                "chat_session_id": chat_session.id,
                "project_id": chat_session.project.id,
                "user_id": chat_session.project.user_id,
            }

    def get_session_context_for_user(self, chat_session_id, user_id):
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession)
                .join(Project, ChatSession.project_id == Project.id)
                .where(ChatSession.id == chat_session_id, Project.user_id == user_id)
                .options(joinedload(ChatSession.project))
            )
            if chat_session is None or chat_session.project is None:
                return None
            return {
                "chat_session_id": chat_session.id,
                "project_id": chat_session.project.id,
                "user_id": chat_session.project.user_id,
            }

    def list_chat_sessions_checked(self, project_id, limit=50):
        with self._session() as session:
            if (
                session.scalar(select(Project.id).where(Project.id == project_id))
                is None
            ):
                return None
            stmt = (
                select(ChatSession)
                .where(ChatSession.project_id == project_id)
                .order_by(ChatSession.updated_at.desc())
                .limit(max(1, int(limit)))
            )
            return list(session.scalars(stmt))

    def list_messages_checked(self, chat_session_id):
        with self._session() as session:
            if (
                session.scalar(
                    select(ChatSession.id).where(ChatSession.id == chat_session_id)
                )
                is None
            ):
                return None
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            return list(session.scalars(stmt))

    def list_messages_for_user(self, chat_session_id, user_id):
        with self._session() as session:
            if (
                session.scalar(
                    select(ChatSession.id)
                    .join(Project, ChatSession.project_id == Project.id)
                    .where(
                        ChatSession.id == chat_session_id, Project.user_id == user_id
                    )
                )
                is None
            ):
                return None
            stmt = (
                select(ChatMessage)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .join(Project, ChatSession.project_id == Project.id)
                .where(
                    ChatMessage.session_id == chat_session_id,
                    Project.user_id == user_id,
                )
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            return list(session.scalars(stmt))

    def list_messages_for_user_chat(self, user_id, chat_session_id):
        return self.list_messages_for_user(chat_session_id, user_id)

    def load_conversation_history(self, chat_session_id):
        with self._session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            rows = list(session.scalars(stmt))
            return [(row.role, row.content) for row in rows]

    def load_conversation_history_for_user(self, chat_session_id, user_id):
        rows = self.list_messages_for_user(chat_session_id, user_id)
        if rows is None:
            return None
        return [(row.role, row.content) for row in rows]

    def list_messages(self, chat_session_id):
        with self._session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            return list(session.scalars(stmt))

    def get_message(self, message_id):
        with self._session() as session:
            return session.scalar(
                select(ChatMessage).where(ChatMessage.id == message_id)
            )

    def append_message(self, chat_session_id, role, content, council_entries=None):
        with self._session() as session:
            chat_session = session.scalar(
                select(ChatSession).where(ChatSession.id == chat_session_id)
            )
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            message = ChatMessage(
                session_id=chat_session_id,
                role=role,
                content=content,
                council_entries=council_entries or None,
            )
            chat_session.updated_at = _utc_now()
            session.add(message)
            session.commit()
            session.refresh(message)
            return message

    def append_message_fast(self, chat_session_id, role, content, council_entries=None):
        """Like append_message but skips the session existence re-check (caller already validated)."""
        with self._session() as session:
            message = ChatMessage(
                session_id=chat_session_id,
                role=role,
                content=content,
                council_entries=council_entries or None,
            )
            session.add(message)
            session.execute(
                update(ChatSession)
                .where(ChatSession.id == chat_session_id)
                .values(updated_at=_utc_now())
            )
            session.commit()
            session.refresh(message)
            return message

    def bootstrap_user(self, username):
        username = (username or "").strip()
        if not username:
            raise ValueError("username is required")
        with self._session() as session:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username)
                session.add(user)
                session.flush()
                session.refresh(user)
            session.commit()
            session.refresh(user)
            return self.bootstrap_for_user(user.id)

    def bootstrap_for_user(self, user_id):
        with self._session() as session:
            user = session.scalar(select(User).where(User.id == user_id))
            if user is None:
                raise ValueError(f"Unknown user '{user_id}'")
            projects = list(
                session.scalars(
                    select(Project)
                    .where(Project.user_id == user_id)
                    .order_by(Project.updated_at.desc())
                    .options(selectinload(Project.chat_sessions))
                )
            )

            bootstrap_projects = []
            chats_by_project = {}
            for project in projects:
                bootstrap_projects.append(
                    {
                        "id": project.id,
                        "user_id": project.user_id,
                        "name": project.name,
                        "description": project.description or "",
                        "created_at": project.created_at,
                        "updated_at": project.updated_at,
                    }
                )
                chats = sorted(
                    project.chat_sessions, key=lambda c: c.updated_at, reverse=True
                )[:50]
                chats_by_project[project.id] = [
                    {
                        "id": chat.id,
                        "project_id": chat.project_id,
                        "title": (chat.title or "").strip(),
                        "created_at": chat.created_at,
                        "updated_at": chat.updated_at,
                    }
                    for chat in chats
                ]

            return {
                "user_id": user.id,
                "user_username": user.username or "",
                "user_role": user.role,
                "user_email": user.email or "",
                "user_email_verified": bool(user.email_verified),
                "user_display_name": user.display_name or "",
                "user_avatar_url": user.avatar_url or "",
                "projects": bootstrap_projects,
                "chats_by_project": chats_by_project,
            }

    def bootstrap_app_session(self, user_id):
        try:
            result = self.bootstrap_for_user(user_id)
        except ValueError:
            return None
        user = self.get_user(user_id)
        return {
            "user": user,
            "projects": result["projects"],
            "chats_by_project": result["chats_by_project"],
        }
