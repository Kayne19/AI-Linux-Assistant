"""Dashboard CLI for the mass-ingestion FSM (T14).

Walks ``ingest_state/`` and the ``failed/`` quarantine dir and prints a
summary by FSM state. Useful for "how many docs are still waiting on
batch?" without grepping JSON.

Usage::

    python scripts/ingest/status.py                # default ingest_state/
    python scripts/ingest/status.py --state-dir alt
    python scripts/ingest/status.py --json         # machine-readable
    python scripts/ingest/status.py --verbose      # one row per doc
"""

from __future__ import annotations

import argparse
import json as _json
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"
for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ingestion.doc_state import DocState, iter_states


# Canonical ordering of FSM states for dashboard rows. Values match the
# constants in ``app.ingestion.batch_runner``.
STATE_ORDER: tuple[str, ...] = (
    "AWAITING_ENRICHMENT",
    "ENRICH_SUBMIT",
    "ENRICH_POLL",
    "ENRICH_MERGE",
    "FINALIZE_OUTPUT",
    "INGEST_VECTOR_DB",
    "CLEANUP_ARTIFACTS",
    "COMPLETED",
    "FAILED",
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Print mass-ingestion FSM status counts.")
    p.add_argument(
        "--state-dir",
        type=Path,
        default=BACKEND_DIR / "ingest_state",
        help="Per-document state directory. Default: Back-end/ingest_state.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit JSON instead of a human-readable table.",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print one line per document below the summary.",
    )
    return p


def _collect_states(state_dir: Path) -> tuple[Counter, list[DocState], list[Path]]:
    """Return (state_counts, doc_states, quarantined_paths)."""
    counts: Counter = Counter()
    docs: list[DocState] = []
    for _doc_id, state in iter_states(state_dir):
        counts[state.state] += 1
        docs.append(state)

    failed_dir = state_dir.parent / "failed"
    quarantined: list[Path] = []
    if failed_dir.exists():
        for child in sorted(failed_dir.iterdir()):
            if child.is_dir():
                quarantined.append(child)
                counts["QUARANTINED"] += 1

    return counts, docs, quarantined


def _render_human(state_dir: Path, counts: Counter, docs: list[DocState], quarantined: list[Path], verbose: bool) -> None:
    total = sum(counts.values())
    print(f"Ingest state directory: {state_dir}")
    print(f"Total documents tracked: {total}")
    print()
    if total == 0:
        print("No documents under tracking. Run scripts/ingest/ingest_pipeline.py --batch-mode <pdf>.")
        return

    rows = list(STATE_ORDER) + ["QUARANTINED"]
    width = max(len(r) for r in rows)
    print(f"{'State'.ljust(width)}  Count")
    print(f"{'-' * width}  -----")
    for state_name in rows:
        n = counts.get(state_name, 0)
        if n == 0:
            continue
        print(f"{state_name.ljust(width)}  {n}")

    if verbose and docs:
        print()
        print("Documents:")
        for state in sorted(docs, key=lambda s: (s.state, s.doc_id)):
            extras = []
            if state.batch_id:
                extras.append(f"batch={state.batch_id}")
            if state.batch_status:
                extras.append(f"openai={state.batch_status}")
            if state.error:
                extras.append(f"error={state.error[:60]}")
            tail = (" " + " ".join(extras)) if extras else ""
            print(f"  [{state.state}] {state.doc_id}{tail}")

    if verbose and quarantined:
        print()
        print("Quarantined:")
        for path in quarantined:
            print(f"  - {path.name}")


def _render_json(state_dir: Path, counts: Counter, docs: list[DocState], quarantined: list[Path]) -> None:
    payload = {
        "state_dir": str(state_dir),
        "totals": {state: counts.get(state, 0) for state in STATE_ORDER},
        "quarantined": [p.name for p in quarantined],
        "documents": [
            {
                "doc_id": state.doc_id,
                "state": state.state,
                "batch_id": state.batch_id,
                "batch_status": state.batch_status,
                "total_chunks": state.total_chunks,
                "completed_chunks": state.completed_chunks,
                "failed_chunks": state.failed_chunks,
                "error": state.error,
                "updated_at": state.updated_at,
            }
            for state in sorted(docs, key=lambda s: (s.state, s.doc_id))
        ],
    }
    print(_json.dumps(payload, indent=2))


def main() -> int:
    args = _build_parser().parse_args()
    state_dir = args.state_dir
    if not state_dir.exists():
        if args.as_json:
            print(_json.dumps({"state_dir": str(state_dir), "totals": {}, "documents": [], "quarantined": []}))
        else:
            print(f"State directory does not exist yet: {state_dir}")
        return 0

    counts, docs, quarantined = _collect_states(state_dir)
    if args.as_json:
        _render_json(state_dir, counts, docs, quarantined)
    else:
        _render_human(state_dir, counts, docs, quarantined, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
