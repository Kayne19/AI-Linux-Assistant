from datetime import datetime, timezone

from persistence.database import get_session_factory

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    select = None

from persistence.postgres_models import ChatMessage, ChatSession, Project, User


def _utc_now():
    return datetime.now(timezone.utc)


class PostgresAppStore:
    def __init__(self, session_factory=None):
        if select is None:
            raise ImportError(
                "SQLAlchemy is required for PostgresAppStore. "
                "Install sqlalchemy and alembic in the AI-Linux-Assistant environment."
            )
        self.session_factory = session_factory or get_session_factory()

    def _session(self):
        return self.session_factory()

    def get_user_by_username(self, username):
        username = (username or "").strip()
        if not username:
            return None
        with self._session() as session:
            return session.scalar(select(User).where(User.username == username))

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

    def list_projects(self, user_id):
        with self._session() as session:
            stmt = select(Project).where(Project.user_id == user_id).order_by(Project.updated_at.desc())
            return list(session.scalars(stmt))

    def create_project(self, user_id, name, description=""):
        name = (name or "").strip()
        if not name:
            raise ValueError("project name is required")
        with self._session() as session:
            project = Project(user_id=user_id, name=name, description=(description or "").strip())
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

    def delete_project(self, project_id):
        with self._session() as session:
            project = session.scalar(select(Project).where(Project.id == project_id))
            if project is None:
                raise ValueError(f"Unknown project '{project_id}'")
            session.delete(project)
            session.commit()

    def create_chat_session(self, project_id, title=""):
        with self._session() as session:
            chat_session = ChatSession(project_id=project_id, title=(title or "").strip())
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

    def get_chat_session(self, chat_session_id):
        with self._session() as session:
            return session.scalar(select(ChatSession).where(ChatSession.id == chat_session_id))

    def update_chat_session_title(self, chat_session_id, title):
        title = (title or "").strip()
        if not title:
            raise ValueError("chat title is required")
        with self._session() as session:
            chat_session = session.scalar(select(ChatSession).where(ChatSession.id == chat_session_id))
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            chat_session.title = title
            chat_session.updated_at = _utc_now()
            session.commit()
            session.refresh(chat_session)
            return chat_session

    def delete_chat_session(self, chat_session_id):
        with self._session() as session:
            chat_session = session.scalar(select(ChatSession).where(ChatSession.id == chat_session_id))
            if chat_session is None:
                raise ValueError(f"Unknown chat session '{chat_session_id}'")
            session.delete(chat_session)
            session.commit()

    def get_session_context(self, chat_session_id):
        chat_session = self.get_chat_session(chat_session_id)
        if chat_session is None:
            return None
        project = self.get_project(chat_session.project_id)
        if project is None:
            return None
        return {
            "chat_session_id": chat_session.id,
            "project_id": project.id,
            "user_id": project.user_id,
        }

    def load_conversation_history(self, chat_session_id):
        with self._session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            rows = list(session.scalars(stmt))
            return [(row.role, row.content) for row in rows]

    def list_messages(self, chat_session_id):
        with self._session() as session:
            stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == chat_session_id)
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
            )
            return list(session.scalars(stmt))

    def append_message(self, chat_session_id, role, content, council_entries=None):
        with self._session() as session:
            chat_session = session.scalar(select(ChatSession).where(ChatSession.id == chat_session_id))
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
