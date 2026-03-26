import argparse
import json
import sys
import time
from pathlib import Path

from pypdf import PdfReader
from tqdm import tqdm

from chatGPT_PDF_intake import process_pdf_parallel
from chatGPT_cleaner import clean_elements
from context_enrichment import enrich_elements


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
APP_DIR = BACKEND_DIR / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from gemini_caller import GeminiWorker
from local_caller import LocalWorker
from openAI_caller import OpenAIWorker
from prompts import REGISTRY_UPDATE_SYSTEM_PROMPT
from routing_registry import load_registry, merge_domain_suggestion
from settings import SETTINGS
from vectorDB import VectorDB


def export_full_text(pdf_path: Path, output_path: Path) -> None:
    reader = PdfReader(str(pdf_path))
    full_content = ""

    print(f"📝 Dumping full text to {output_path.name}...")
    for page in tqdm(reader.pages, desc="Exporting Text", unit="page"):
        full_content += (page.extract_text() or "") + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_content, encoding="utf-8")
    print(f"✅ Text dump saved! Size: {len(full_content)} chars.")


def write_json(output_path: Path, data) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_json_object(text: str):
    text = (text or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _prompt_with_default(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def _normalize_whitespace(text: str) -> str:
    return " ".join((text or "").split())


def _clean_pdf_meta_value(value) -> str:
    if value is None:
        return ""
    return _normalize_whitespace(str(value))


def _looks_like_heading(line: str) -> bool:
    line = _normalize_whitespace(line)
    if not line:
        return False
    if len(line) < 5 or len(line) > 120:
        return False
    if line.endswith((".", ",", ";", ":")):
        return False

    words = line.split()
    if len(words) > 14:
        return False

    alpha_words = [word for word in words if any(ch.isalpha() for ch in word)]
    if not alpha_words:
        return False

    titleish_words = 0
    for word in alpha_words:
        stripped = word.strip("()[]{}<>-_/\\")
        if not stripped:
            continue
        if stripped.isupper() or stripped[:1].isupper():
            titleish_words += 1

    return (titleish_words / len(alpha_words)) >= 0.7


def extract_document_identity(pdf_path: Path):
    reader = PdfReader(str(pdf_path))
    metadata = reader.metadata or {}

    first_page_samples = []
    heading_candidates = []
    seen_headings = set()

    for page_index, page in enumerate(reader.pages[:5]):
        page_text = page.extract_text() or ""
        normalized_page = _normalize_whitespace(page_text)
        if normalized_page:
            first_page_samples.append(normalized_page[:1200])

        for raw_line in page_text.splitlines():
            line = _normalize_whitespace(raw_line)
            if not _looks_like_heading(line):
                continue
            dedupe_key = line.lower()
            if dedupe_key in seen_headings:
                continue
            seen_headings.add(dedupe_key)
            heading_candidates.append(line)
            if len(heading_candidates) >= 20:
                break
        if len(heading_candidates) >= 20:
            break

    return {
        "filename": pdf_path.name,
        "stem": pdf_path.stem,
        "metadata": {
            "title": _clean_pdf_meta_value(getattr(metadata, "title", None) or metadata.get("/Title")),
            "subject": _clean_pdf_meta_value(getattr(metadata, "subject", None) or metadata.get("/Subject")),
            "author": _clean_pdf_meta_value(getattr(metadata, "author", None) or metadata.get("/Author")),
            "producer": _clean_pdf_meta_value(getattr(metadata, "producer", None) or metadata.get("/Producer")),
        },
        "front_matter_samples": first_page_samples[:3],
        "heading_candidates": heading_candidates[:15],
    }


def summarize_registry_suggestion(suggestion) -> str:
    action = suggestion.get("action")
    if action == "upsert":
        aliases = ", ".join(suggestion.get("aliases", [])) or "-"
        description = suggestion.get("description", "") or "-"
        return (
            f"action=upsert\n"
            f"label={suggestion.get('label', '')}\n"
            f"aliases={aliases}\n"
            f"description={description}"
        )
    if action == "skip":
        return f"action=skip\nreason={suggestion.get('reason', 'not provided')}"
    return json.dumps(suggestion, indent=2)


def review_registry_suggestion(suggestion, document_identity):
    print("🧾 Registry suggestion:")
    print(summarize_registry_suggestion(suggestion))

    if not sys.stdin.isatty():
        return suggestion

    print("Press Enter to accept, 'e' to edit, or 's' to skip.")
    choice = input("Registry action [accept/e/s]: ").strip().lower()

    if choice in {"", "accept", "a"}:
        return suggestion
    if choice in {"s", "skip"}:
        return {"action": "skip", "reason": "manual override"}
    if choice not in {"e", "edit"}:
        print("⚠️ Unrecognized choice. Accepting model suggestion.")
        return suggestion

    default_label = suggestion.get("label", "") if suggestion.get("action") == "upsert" else ""
    if not default_label:
        default_label = _normalize_whitespace(document_identity.get("stem", "")).lower().replace(" ", "_")

    default_aliases = suggestion.get("aliases", []) if suggestion.get("action") == "upsert" else []
    if not default_aliases:
        default_aliases = [document_identity.get("filename", ""), document_identity.get("stem", "")]
    default_aliases_text = ", ".join(alias for alias in default_aliases if alias)

    default_description = suggestion.get("description", "") if suggestion.get("action") == "upsert" else ""
    if not default_description:
        default_description = (
            document_identity.get("metadata", {}).get("title")
            or document_identity.get("metadata", {}).get("subject")
            or "Document-specific domain"
        )

    label = _prompt_with_default("Label", default_label)
    aliases_text = _prompt_with_default("Aliases (comma-separated)", default_aliases_text)
    description = _prompt_with_default("Description", default_description)
    aliases = [alias.strip() for alias in aliases_text.split(",") if alias.strip()]

    manual_suggestion = {
        "action": "upsert",
        "label": label,
        "aliases": aliases,
        "description": description,
    }

    print("📝 Manual registry override:")
    print(summarize_registry_suggestion(manual_suggestion))
    return manual_suggestion


REGISTRY_WORKER_TYPES = {
    "openai": OpenAIWorker,
    "local": LocalWorker,
    "gemini": GeminiWorker,
}


def build_registry_worker(provider: str, model: str):
    worker_class = REGISTRY_WORKER_TYPES.get(provider.lower())
    if worker_class is None:
        raise ValueError(f"Unknown registry updater provider '{provider}'")
    return worker_class(model=model)


def update_routing_registry(
    pdf_path: Path,
    context_output: Path,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    context_text = context_output.read_text(encoding="utf-8")
    front_excerpt = context_text[:6000]
    tail_excerpt = context_text[-2500:] if len(context_text) > 6000 else ""
    document_identity = extract_document_identity(pdf_path)
    registry = load_registry()
    if provider is None:
        provider = SETTINGS.registry_updater.provider
    if model is None:
        model = SETTINGS.registry_updater.model
    worker = build_registry_worker(provider, model)

    user_message = f"""
    <existing_registry>
    {json.dumps(registry, indent=2)}
    </existing_registry>

    <document_identity>
    {json.dumps(document_identity, indent=2)}
    </document_identity>

    <document_front_excerpt>
    {front_excerpt}
    </document_front_excerpt>

    <document_tail_excerpt>
    {tail_excerpt}
    </document_tail_excerpt>
    """

    print("🧭 Updating routing registry...")
    response = worker.generate_text(
        system_prompt=REGISTRY_UPDATE_SYSTEM_PROMPT,
        user_message=user_message,
        history=[],
        temperature=0.1,
    )

    suggestion = extract_json_object(response)
    if not suggestion:
        print("⚠️ Registry update skipped: could not parse local model output.")
        return

    suggestion = review_registry_suggestion(suggestion, document_identity)

    if suggestion.get("action") == "skip":
        print(f"ℹ️ Registry unchanged: {suggestion.get('reason', 'not needed')}")
        return

    if suggestion.get("action") != "upsert":
        print("⚠️ Registry update skipped: unrecognized action.")
        return

    changed, message = merge_domain_suggestion(suggestion)
    if changed:
        print(f"✅ Routing registry updated: {message}")
    else:
        print(f"ℹ️ Routing registry unchanged: {message}")


def run_pipeline(
    pdf_path: Path,
    raw_output: Path,
    clean_output: Path,
    context_output: Path,
    final_output: Path,
    batch_size: int,
    max_workers: int,
    hi_res_model_name: str,
    min_text_chars: int,
    ocr_dpi: int,
    enrichment_model: str,
    registry_provider: str,
    registry_model: str,
) -> None:
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    start_time = time.time()

    print(f"📄 Intake: {pdf_path}")
    raw_elements = process_pdf_parallel(
        str(pdf_path),
        batch_size=batch_size,
        max_workers=max_workers,
        hi_res_model_name=hi_res_model_name,
        min_text_chars=min_text_chars,
        ocr_dpi=ocr_dpi,
    )
    write_json(raw_output, raw_elements)
    print(f"💾 Wrote: {raw_output.name}")

    export_full_text(pdf_path, context_output)
    update_routing_registry(
        pdf_path,
        context_output,
        provider=registry_provider,
        model=registry_model,
    )

    print("🧹 Cleaning extracted elements...")
    cleaned_elements = clean_elements(raw_elements, drop_boilerplate=False)
    write_json(clean_output, cleaned_elements)
    print(f"✅ Cleaned: {len(raw_elements)} -> {len(cleaned_elements)} elements")
    print(f"💾 Wrote: {clean_output.name}")

    print("🧠 Enriching chunks...")
    enrich_elements(
        json_path=str(clean_output),
        context_text_path=str(context_output),
        model=enrichment_model,
    )

    generated_final_output = clean_output.with_name(f"{clean_output.stem}_final.json")
    if generated_final_output != final_output:
        final_output.parent.mkdir(parents=True, exist_ok=True)
        generated_final_output.replace(final_output)

    if not final_output.exists():
        raise FileNotFoundError(final_output)

    print("🗃️ Ingesting into LanceDB...")
    vector_db = VectorDB()
    vector_db.JSON_PATH = str(final_output)
    vector_db.ingest_data()

    duration = time.time() - start_time
    print(f"🏁 Full pipeline finished in {duration:.2f}s")


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
    parser.add_argument("--enrichment-model", default="mannix/llama3.1-8b-abliterated")
    parser.add_argument("--registry-provider", default=SETTINGS.registry_updater.provider)
    parser.add_argument("--registry-model", default=SETTINGS.registry_updater.model)
    return parser


def resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    pdf_path_arg = args.pdf_path

    if not pdf_path_arg:
        print("Run directly with a path, for example:")
        print("  python scripts/ingest_pipeline.py data/The_Linux_Command_Line.pdf")
        pdf_path_arg = input("PDF path: ").strip()
        if not pdf_path_arg:
            raise SystemExit("No PDF path provided.")

    run_pipeline(
        pdf_path=resolve_path(pdf_path_arg),
        raw_output=resolve_path(args.raw_output),
        clean_output=resolve_path(args.clean_output),
        context_output=resolve_path(args.context_output),
        final_output=resolve_path(args.final_output),
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        hi_res_model_name=args.hi_res_model_name,
        min_text_chars=args.min_text_chars,
        ocr_dpi=args.ocr_dpi,
        enrichment_model=args.enrichment_model,
        registry_provider=args.registry_provider,
        registry_model=args.registry_model,
    )
