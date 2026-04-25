"""CLI wrapper around ingestion.batch_runner.run_once.

Intended to be invoked from cron or by hand::

    python scripts/ingest/batch_runner.py --state-dir Back-end/ingest_state

Every call walks the state directory once and advances each parked doc as far
as possible. Safe to re-enter — all state is durable on disk.
"""

import argparse
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"

for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ingestion.batch_runner import run_once


def _build_indexer_fn():
    from ingestion.indexer import build_ingestion_indexer
    from retrieval.config import load_retrieval_config

    retrieval_config = load_retrieval_config()
    indexer = build_ingestion_indexer(retrieval_config)

    def _ingest(path: str, document_identity=None) -> dict:
        return indexer.ingest_json(path, document_identity=document_identity)

    return _ingest


def main() -> int:
    parser = argparse.ArgumentParser(description="Advance parked ingest docs through Phase 2.")
    parser.add_argument("--state-dir", default="Back-end/ingest_state", help="Directory containing parked doc state")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    base_dir = Path(args.state_dir)
    if not base_dir.exists():
        print(f"No state directory at {base_dir}; nothing to do.")
        return 0

    report = run_once(base_dir, indexer_fn=_build_indexer_fn())
    print(
        "batch_runner pass:",
        {
            "submitted": len(report.submitted),
            "polled": len(report.polled),
            "completed": len(report.completed),
            "quarantined": len(report.quarantined),
            "errors": len(report.errors),
        },
    )
    if report.errors:
        for doc_id, err in report.errors:
            print(f"  error: {doc_id} -> {err}")
    if report.quarantined:
        for doc_id, reason in report.quarantined:
            print(f"  quarantined: {doc_id} -> {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
