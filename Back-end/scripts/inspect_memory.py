"""Inspect committed and unresolved memory items.

Usage:
    python scripts/inspect_memory.py
    python scripts/inspect_memory.py --db /path/to/assistant_memory.db
"""

import argparse
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
APP_DIR = BACKEND_DIR / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from memory_store import MemoryStore


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect committed and unresolved assistant memory.")
    parser.add_argument("--db", help="Optional path to a specific memory database.")
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
    store = MemoryStore(db_path=args.db) if args.db else MemoryStore()
    print(store.format_debug_dump(query=args.query, max_candidates=args.max_candidates))


if __name__ == "__main__":
    main()
