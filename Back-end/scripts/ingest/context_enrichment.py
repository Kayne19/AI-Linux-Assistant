import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"

for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.ingestion.console import print_banner
from app.ingestion.pipeline import build_text_worker
from app.ingestion.stages.context_enrichment import enrich_elements
from app.config.settings import SETTINGS


def main() -> int:
    print_banner("CONTEXT ENRICHMENT SCRIPT", [f"Input: extracted_clean.json"], char="=")
    worker = build_text_worker(
        SETTINGS.ingest_enricher.provider,
        SETTINGS.ingest_enricher.model,
        SETTINGS.ingest_enricher.reasoning_effort,
    )
    enrich_elements(
        json_path="extracted_clean.json",
        context_text_path="doc_context.txt",
        worker=worker,
        model=SETTINGS.ingest_enricher.model,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
