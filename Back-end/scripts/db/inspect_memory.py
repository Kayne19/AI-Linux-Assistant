"""Inspect committed and unresolved Postgres-backed memory items.

Usage:
    python scripts/db/inspect_memory.py --username kayne19 --list-projects
    python scripts/db/inspect_memory.py --project-id <project-id>
"""

import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent
APP_DIR = BACKEND_DIR / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from persistence.postgres_app_store import PostgresAppStore
from persistence.postgres_memory_store import PostgresMemoryStore


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect committed and unresolved Postgres-backed assistant memory.")
    parser.add_argument("--project-id", help="Project ID to inspect.")
    parser.add_argument("--username", help="Username used with --list-projects.")
    parser.add_argument(
        "--list-projects",
        action="store_true",
        help="List projects for the given --username instead of printing a memory dump.",
    )
    parser.add_argument(
        "--query",
        default="system profile attempts issues preferences constraints",
        help="Query used to build the relevant-memory view.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help="Maximum number of unresolved candidate/conflict rows to show.",
    )
    return parser


def main():
    args = build_parser().parse_args()
    app_store = PostgresAppStore()

    if args.list_projects:
        if not args.username:
            raise SystemExit("--username is required with --list-projects")
        user = app_store.get_user_by_username(args.username)
        if user is None:
            raise SystemExit(f"Unknown username: {args.username}")
        projects = app_store.list_projects(user.id)
        if not projects:
            print(f"No projects found for {user.username}")
            return
        for project in projects:
            print(f"{project.id}  {project.name}")
        return

    if not args.project_id:
        raise SystemExit("--project-id is required unless --list-projects is used")

    store = PostgresMemoryStore(project_id=args.project_id)
    print(store.format_debug_dump(query=args.query, max_candidates=args.max_candidates))


if __name__ == "__main__":
    main()
