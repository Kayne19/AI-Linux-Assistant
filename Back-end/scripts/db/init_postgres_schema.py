import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
APP_DIR = ROOT_DIR / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from persistence.database import build_engine  # noqa: E402
from persistence.postgres_app_store import PostgresAppStore  # noqa: E402
from persistence.postgres_models import Base  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="Initialize the Postgres schema for the AI Linux Assistant.")
    parser.add_argument("--database-url", default="", help="Override DATABASE_URL for this run.")
    parser.add_argument("--username", default="", help="Optional username to create.")
    parser.add_argument("--project", default="", help="Optional project name to create under the username.")
    parser.add_argument("--session-title", default="", help="Optional chat session title to create.")
    return parser.parse_args()


def main():
    args = parse_args()
    engine = build_engine(args.database_url or None)
    Base.metadata.create_all(engine)
    print("Initialized schema.")

    if not args.username:
        return

    store = PostgresAppStore()
    user = store.find_or_create_user(args.username)
    print(f"User: {user.username} ({user.id})")

    if not args.project:
        return

    project = None
    for candidate in store.list_projects(user.id):
        if candidate.name == args.project:
            project = candidate
            break
    if project is None:
        project = store.create_project(user.id, args.project)
    print(f"Project: {project.name} ({project.id})")

    if args.session_title:
        chat_session = store.create_chat_session(project.id, title=args.session_title)
        print(f"Chat session: {chat_session.id}")


if __name__ == "__main__":
    main()
