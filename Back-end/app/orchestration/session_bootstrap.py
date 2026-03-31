from dataclasses import dataclass

from orchestration.model_router import ModelRouter
from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_memory_store import PostgresMemoryStore


@dataclass
class SessionBootstrap:
    username: str
    user_id: str
    project_id: str
    project_name: str
    chat_session_id: str
    chat_session_title: str
    app_store: PostgresAppStore

    def build_router(self):
        memory_store = PostgresMemoryStore(project_id=self.project_id)
        return ModelRouter(
            memory_store=memory_store,
            chat_store=self.app_store,
            chat_session_id=self.chat_session_id,
        )


def _read_nonempty(prompt_text):
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        print("Please enter a value.")


def _choose_from_list(items, item_label, display_fn):
    if not items:
        return None

    while True:
        for index, item in enumerate(items, 1):
            print(f"{index}. {display_fn(item)}")
        choice = input(f"Choose a {item_label} by number, or type 'n' for new: ").strip().lower()
        if choice == "n":
            return None
        try:
            return items[int(choice) - 1]
        except Exception:
            print(f"Invalid {item_label} selection.")


def _display_project(project):
    updated_at = getattr(project, "updated_at", None)
    stamp = updated_at.isoformat() if updated_at is not None else "unknown"
    return f"{project.name}  [{stamp}]"


def _display_chat_session(chat_session):
    title = (chat_session.title or "").strip() or "(untitled chat)"
    updated_at = getattr(chat_session, "updated_at", None)
    stamp = updated_at.isoformat() if updated_at is not None else "unknown"
    return f"{title}  [{stamp}]"


def bootstrap_interactive_session():
    app_store = PostgresAppStore()

    print("\nAI Linux Assistant")
    username = _read_nonempty("Username: ")
    user = app_store.find_or_create_user(username)

    print(f"\nProjects for {user.username}:")
    projects = app_store.list_projects(user.id)
    project = _choose_from_list(projects, "project", _display_project)
    if project is None:
        project_name = _read_nonempty("New project name: ")
        project_description = input("Project description (optional): ").strip()
        project = app_store.create_project(user.id, project_name, description=project_description)
        print(f"Created project: {project.name}")

    print(f"\nChats for project '{project.name}':")
    chat_sessions = app_store.list_chat_sessions(project.id)
    chat_session = _choose_from_list(chat_sessions, "chat", _display_chat_session)
    if chat_session is None:
        title = input("New chat title (optional): ").strip()
        chat_session = app_store.create_chat_session(project.id, title=title)
        print(f"Created chat session: {chat_session.id}")

    return SessionBootstrap(
        username=user.username,
        user_id=user.id,
        project_id=project.id,
        project_name=project.name,
        chat_session_id=chat_session.id,
        chat_session_title=(chat_session.title or "").strip(),
        app_store=app_store,
    )
