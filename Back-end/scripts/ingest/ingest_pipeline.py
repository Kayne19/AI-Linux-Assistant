import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
BACKEND_DIR = SCRIPTS_DIR.parent
APP_DIR = BACKEND_DIR / "app"

for path in (BACKEND_DIR, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.config.settings import SETTINGS
from app.ingestion.pipeline import IngestPipelineConfig, run_directory_queue, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full PDF -> clean -> enrich -> LanceDB pipeline.")
    parser.add_argument("pdf_path", nargs="?", help="Path to the PDF to ingest, relative to Back-end/ or absolute.")
    parser.add_argument("--raw-output", default="extracted_raw.json")
    parser.add_argument("--clean-output", default="extracted_clean.json")
    parser.add_argument("--context-output", default="doc_context.txt")
    parser.add_argument("--final-output", default="extracted_clean_final.json")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--hi-res-model-name", default="yolox")
    parser.add_argument("--min-text-chars", type=int, default=50)
    parser.add_argument("--ocr-dpi", type=int, default=300)
    parser.add_argument("--enrichment-provider", default=SETTINGS.ingest_enricher.provider)
    parser.add_argument("--enrichment-model", default=SETTINGS.ingest_enricher.model)
    parser.add_argument("--enrichment-reasoning-effort", default=SETTINGS.ingest_enricher.reasoning_effort or "")
    parser.add_argument("--registry-provider", default=SETTINGS.registry_updater.provider)
    parser.add_argument("--registry-model", default=SETTINGS.registry_updater.model)
    parser.add_argument("--trace-output-dir", default="ingest_traces")
    return parser


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


def build_config(args) -> IngestPipelineConfig:
    return IngestPipelineConfig(
        raw_output=resolve_path(args.raw_output),
        clean_output=resolve_path(args.clean_output),
        context_output=resolve_path(args.context_output),
        final_output=resolve_path(args.final_output),
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        hi_res_model_name=args.hi_res_model_name,
        min_text_chars=args.min_text_chars,
        ocr_dpi=args.ocr_dpi,
        enrichment_provider=args.enrichment_provider,
        enrichment_model=args.enrichment_model,
        enrichment_reasoning_effort=args.enrichment_reasoning_effort or None,
        registry_provider=args.registry_provider,
        registry_model=args.registry_model,
        trace_output_dir=resolve_path(args.trace_output_dir),
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    pdf_path_arg = args.pdf_path

    if not pdf_path_arg:
        print("Run directly with a path, for example:")
        print("  python scripts/ingest/ingest_pipeline.py data/The_Linux_Command_Line.pdf")
        print("  python scripts/ingest/ingest_pipeline.py /path/to/queue_root")
        pdf_path_arg = input("PDF path: ").strip()
        if not pdf_path_arg:
            raise SystemExit("No PDF path provided.")

    target_path = resolve_path(pdf_path_arg)
    config = build_config(args)

    if target_path.is_dir():
        run_directory_queue(target_path, config)
        return 0

    run_pipeline(
        pdf_path=target_path,
        raw_output=config.raw_output,
        clean_output=config.clean_output,
        context_output=config.context_output,
        final_output=config.final_output,
        batch_size=config.batch_size,
        max_workers=config.max_workers,
        hi_res_model_name=config.hi_res_model_name,
        min_text_chars=config.min_text_chars,
        ocr_dpi=config.ocr_dpi,
        enrichment_provider=config.enrichment_provider,
        enrichment_model=config.enrichment_model,
        enrichment_reasoning_effort=config.enrichment_reasoning_effort,
        registry_provider=config.registry_provider,
        registry_model=config.registry_model,
        trace_output_dir=config.trace_output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
