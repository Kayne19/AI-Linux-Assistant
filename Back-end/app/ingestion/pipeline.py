import json
import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class LowPageCoverageError(Exception):
    """Raised when intake page coverage falls below the configured threshold.

    Caught at the queue level so the bad document is quarantined without
    killing the rest of the queue run.
    """

from pypdf import PdfReader

from config.settings import SETTINGS
from ingestion.audit import AuditLog
from ingestion.console import print_artifact, print_banner, print_kv, print_progress, print_state, print_summary
from ingestion.indexer import build_ingestion_indexer
from ingestion.stages.cleaner import clean_elements
from ingestion.stages.context_enrichment import enrich_elements
from ingestion.stages.pdf_intake import IntakeResult, process_pdf_parallel
from ingestion.stages.sanitizer import sanitize_pdf
from ingestion.trace import IngestTraceRecorder
from orchestration.routing_registry import load_registry, merge_domain_suggestion
from prompting.prompts import REGISTRY_UPDATE_SYSTEM_PROMPT
from providers.anthropic_caller import AnthropicWorker
from providers.google_caller import GoogleWorker
from providers.local_caller import LocalWorker
from providers.openAI_caller import OpenAIWorker
from retrieval.config import load_retrieval_config


REGISTRY_UPDATE_OUTPUT_SCHEMA = {
    "title": "registry_update_output",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["skip", "upsert"]},
        "reason": {"type": "string"},
        "label": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
    },
    "required": ["action"],
}


def export_full_text(pdf_path: Path, output_path: Path) -> None:
    reader = PdfReader(str(pdf_path))
    full_content = ""
    total_pages = len(reader.pages)

    print_state("📝 EXPORT_TEXT", output_path.name)
    for index, page in enumerate(reader.pages, start=1):
        full_content += (page.extract_text() or "") + "\n"
        if index == 1 or index == total_pages or index % 25 == 0:
            print_progress("export pages", index, total_pages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(full_content, encoding="utf-8")
    print_summary(
        "Text export complete",
        [
            ("output", output_path.name),
            ("characters", len(full_content)),
        ],
    )


def write_json(output_path: Path, data) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


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

    for page in reader.pages[:5]:
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


def auto_apply_registry_suggestion(
    suggestion: dict | None,
    document_identity: dict,
    *,
    audit: "AuditLog | None" = None,
) -> dict:
    """Validate + accept an LLM registry suggestion autonomously. Never prompts.

    Returns the suggestion (possibly normalized). If the LLM produced nothing
    usable (None, missing action, or unrecognized action), returns
    {"action": "skip", "reason": "<why>"} so the caller treats it as a no-op.
    """
    doc = document_identity.get("filename", "unknown")

    if suggestion is None:
        result = {"action": "skip", "reason": "parser_failed"}
        if audit is not None:
            audit.record(
                doc=doc,
                phase="registry_update",
                action="reject_missing",
                inputs={"document_identity": document_identity, "suggestion": suggestion},
                chosen=result,
                confidence=None,
                rationale="no LLM output",
            )
        return result

    action = suggestion.get("action")
    if action not in {"upsert", "skip"}:
        result = {"action": "skip", "reason": "unrecognized_action"}
        if audit is not None:
            audit.record(
                doc=doc,
                phase="registry_update",
                action="reject_bad_action",
                inputs={"document_identity": document_identity, "suggestion": suggestion},
                chosen=result,
                confidence=None,
                rationale="unrecognized action from LLM",
            )
        return result

    if action == "skip":
        if audit is not None:
            audit.record(
                doc=doc,
                phase="registry_update",
                action="accept_skip",
                inputs={"document_identity": document_identity, "suggestion": suggestion},
                chosen=suggestion,
                confidence=None,
                rationale="accepted LLM suggestion",
            )
        return suggestion

    # action == "upsert"
    label = suggestion.get("label")
    if not label or not str(label).strip():
        result = {"action": "skip", "reason": "missing_label"}
        if audit is not None:
            audit.record(
                doc=doc,
                phase="registry_update",
                action="reject_missing_label",
                inputs={"document_identity": document_identity, "suggestion": suggestion},
                chosen=result,
                confidence=None,
                rationale="label missing",
            )
        return result

    if audit is not None:
        audit.record(
            doc=doc,
            phase="registry_update",
            action="accept_upsert",
            inputs={"document_identity": document_identity, "suggestion": suggestion},
            chosen=suggestion,
            confidence=None,
            rationale="accepted LLM suggestion",
        )
    return suggestion


# Unused after T5; retained for one release cycle. Remove in follow-up.
def review_registry_suggestion(suggestion, document_identity):
    print("🧾 Registry suggestion")
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

    print("📝 Manual registry override")
    print(summarize_registry_suggestion(manual_suggestion))
    return manual_suggestion


TEXT_WORKER_TYPES = {
    "openai": OpenAIWorker,
    "anthropic": AnthropicWorker,
    "google": GoogleWorker,
    "local": LocalWorker,
}


def build_text_worker(provider: str, model: str, reasoning_effort: str | None = None):
    worker_class = TEXT_WORKER_TYPES.get(provider.lower())
    if worker_class is None:
        raise ValueError(f"Unknown text worker provider '{provider}'")
    if provider.lower() == "openai":
        return worker_class(model=model, reasoning_effort=reasoning_effort)
    return worker_class(model=model)


def update_routing_registry(
    pdf_path: Path,
    context_output: Path,
    provider: str | None = None,
    model: str | None = None,
    *,
    audit: "AuditLog | None" = None,
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
    worker = build_text_worker(provider, model, SETTINGS.registry_updater.reasoning_effort)

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

    print_state("🧭 UPDATE_REGISTRY", pdf_path.name)
    response = worker.generate_text(
        system_prompt=REGISTRY_UPDATE_SYSTEM_PROMPT,
        user_message=user_message,
        history=[],
        temperature=0.1,
        structured_output=True,
        output_schema=REGISTRY_UPDATE_OUTPUT_SCHEMA,
    )

    suggestion = extract_json_object(response)
    if not suggestion:
        print("⚠️ Registry update skipped: could not parse local model output.")
        return

    suggestion = auto_apply_registry_suggestion(suggestion, document_identity, audit=audit)

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


class IngestState(str, Enum):
    INITIALIZED = "INITIALIZED"
    VALIDATE_INPUT = "VALIDATE_INPUT"
    INTAKE_RAW = "INTAKE_RAW"
    EXPORT_TEXT = "EXPORT_TEXT"
    UPDATE_REGISTRY = "UPDATE_REGISTRY"
    CLEAN_ELEMENTS = "CLEAN_ELEMENTS"
    ENRICH_CONTEXT = "ENRICH_CONTEXT"
    FINALIZE_OUTPUT = "FINALIZE_OUTPUT"
    INGEST_VECTOR_DB = "INGEST_VECTOR_DB"
    CLEANUP_ARTIFACTS = "CLEANUP_ARTIFACTS"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class IngestPipelineConfig:
    raw_output: Path
    clean_output: Path
    context_output: Path
    final_output: Path
    batch_size: int
    max_workers: int
    hi_res_model_name: str
    min_text_chars: int
    ocr_dpi: int
    enrichment_provider: str
    enrichment_model: str
    enrichment_reasoning_effort: str | None
    registry_provider: str
    registry_model: str
    trace_output_dir: Path
    # Mass-ingestion robustness fields
    mass_mode: bool = False
    sanitize: bool = False
    min_page_coverage: float = 0.9


@dataclass
class IngestRunContext:
    pdf_path: Path
    config: IngestPipelineConfig
    state_trace: list[str] = field(default_factory=list)
    raw_elements: list[dict[str, Any]] = field(default_factory=list)
    cleaned_elements: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    generated_final_output: Path | None = None
    queue_index: int = 1
    queue_total: int = 1
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    duration_seconds: float = 0.0
    audit: "AuditLog | None" = None
    intake_result: "IntakeResult | None" = None


class IngestPipelineRunner:
    def __init__(self, config: IngestPipelineConfig):
        self.config = config
        self.enrichment_worker = build_text_worker(
            config.enrichment_provider,
            config.enrichment_model,
            config.enrichment_reasoning_effort,
        )

    def run(self, pdf_path: Path, queue_index: int = 1, queue_total: int = 1) -> IngestRunContext:
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%f')}Z_{pdf_path.stem}"
        audit = AuditLog(run_id=run_id, traces_dir=self.config.trace_output_dir)
        context = IngestRunContext(
            pdf_path=pdf_path,
            config=self.config,
            queue_index=queue_index,
            queue_total=queue_total,
            audit=audit,
        )
        try:
            self._print_run_banner(context)
            self._transition(context, IngestState.INITIALIZED)
            self._transition(context, IngestState.VALIDATE_INPUT)
            self._validate_input(context)
            self._transition(context, IngestState.INTAKE_RAW)
            self._intake_raw(context)
            self._transition(context, IngestState.EXPORT_TEXT)
            self._export_text(context)
            self._transition(context, IngestState.UPDATE_REGISTRY)
            self._update_registry(context)
            self._transition(context, IngestState.CLEAN_ELEMENTS)
            self._clean_elements(context)
            self._transition(context, IngestState.ENRICH_CONTEXT)
            self._enrich_context(context)
            self._transition(context, IngestState.FINALIZE_OUTPUT)
            self._finalize_output(context)
            self._transition(context, IngestState.INGEST_VECTOR_DB)
            self._ingest_vector_db(context)
            self._transition(context, IngestState.CLEANUP_ARTIFACTS)
            self._cleanup_artifacts(context)
            self._transition(context, IngestState.COMPLETED)
            duration = time.time() - context.start_time
            context.duration_seconds = duration
            context.completed_at = datetime.now(timezone.utc).isoformat()
            print(
                f"🏁 Completed {self._document_label(context)} in {duration:.2f}s"
                f" | raw={len(context.raw_elements)} | cleaned={len(context.cleaned_elements)}"
            )
        finally:
            audit.close()
        return context

    def _document_label(self, context: IngestRunContext) -> str:
        return f"[{context.queue_index}/{context.queue_total}] {context.pdf_path.name}"

    def _print_run_banner(self, context: IngestRunContext) -> None:
        print_banner(
            f"INGEST DOCUMENT {self._document_label(context)}",
            lines=[f"Source: {context.pdf_path}"],
        )

    def _transition(self, context: IngestRunContext, state: IngestState) -> None:
        context.state_trace.append(state.value)
        print_state(f"[{context.queue_index}/{context.queue_total}] {state.value}", context.pdf_path.name)

    def _validate_input(self, context: IngestRunContext) -> None:
        if not context.pdf_path.exists():
            raise FileNotFoundError(context.pdf_path)

    def _quarantine_doc(
        self,
        context: IngestRunContext,
        *,
        reason: str,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Move the source PDF into data/failed/<stem>/ and write error.json.

        Also emits an audit-log entry and then raises LowPageCoverageError so
        the queue-level loop can cleanly catch it.
        """
        pdf_path = context.pdf_path
        failed_root = pdf_path.parent.parent.parent / "data" / "failed" / pdf_path.stem
        failed_root.mkdir(parents=True, exist_ok=True)

        dest_pdf = failed_root / pdf_path.name
        shutil.copy2(str(pdf_path), str(dest_pdf))

        ts = datetime.now(timezone.utc).isoformat()
        error_data: dict[str, Any] = {"reason": reason, "ts": ts}
        if extra:
            error_data.update(extra)
        error_json_path = failed_root / "error.json"
        with error_json_path.open("w", encoding="utf-8") as fh:
            json.dump(error_data, fh, indent=2, ensure_ascii=False)

        if context.audit is not None:
            context.audit.record(
                doc=pdf_path.name,
                phase="intake_quarantine",
                action=action,
                inputs=error_data,
                chosen=None,
                confidence=None,
                rationale=reason,
            )

        raise LowPageCoverageError(
            f"{pdf_path.name}: quarantined ({action}): {reason}"
        )

    def _intake_raw(self, context: IngestRunContext) -> None:
        config = context.config
        pdf_path = context.pdf_path

        # --- optional sanitizer pass (mass_mode or explicit sanitize=True) ---
        source_for_intake = pdf_path
        if config.sanitize:
            sanitized_path = pdf_path.with_name(f"{pdf_path.stem}_sanitized.pdf")
            ok = sanitize_pdf(pdf_path, sanitized_path)
            if not ok:
                self._quarantine_doc(
                    context,
                    reason="sanitizer failed to rewrite PDF",
                    action="sanitizer_failed",
                )
            source_for_intake = sanitized_path

        intake_result = process_pdf_parallel(
            str(source_for_intake),
            batch_size=config.batch_size,
            max_workers=config.max_workers,
            hi_res_model_name=config.hi_res_model_name,
            min_text_chars=config.min_text_chars,
            ocr_dpi=config.ocr_dpi,
        )
        context.intake_result = intake_result

        # Clean up temporary sanitized file if we created one
        if config.sanitize and source_for_intake != pdf_path and source_for_intake.exists():
            try:
                source_for_intake.unlink()
            except Exception:
                pass

        # --- coverage threshold check ---
        if intake_result.page_coverage_pct < config.min_page_coverage:
            self._quarantine_doc(
                context,
                reason=(
                    f"page coverage {intake_result.page_coverage_pct:.1%} "
                    f"below threshold {config.min_page_coverage:.1%}"
                ),
                action="low_coverage",
                extra={
                    "page_coverage_pct": intake_result.page_coverage_pct,
                    "processed_pages": intake_result.processed_pages,
                    "total_pages": intake_result.total_pages,
                    "failed_batches": intake_result.failed_batches,
                },
            )

        context.raw_elements = intake_result.elements
        write_json(config.raw_output, context.raw_elements)
        print_summary(
            "Raw extraction complete",
            [
                ("elements", len(context.raw_elements)),
                ("artifact", config.raw_output.name),
            ],
        )

    def _export_text(self, context: IngestRunContext) -> None:
        export_full_text(context.pdf_path, context.config.context_output)

    def _update_registry(self, context: IngestRunContext) -> None:
        update_routing_registry(
            context.pdf_path,
            context.config.context_output,
            provider=context.config.registry_provider,
            model=context.config.registry_model,
            audit=context.audit,
        )

    def _clean_elements(self, context: IngestRunContext) -> None:
        drop_boilerplate = context.config.mass_mode
        context.cleaned_elements = clean_elements(context.raw_elements, drop_boilerplate=drop_boilerplate)
        write_json(context.config.clean_output, context.cleaned_elements)
        print_summary(
            "Cleaning complete",
            [
                ("raw", len(context.raw_elements)),
                ("clean", len(context.cleaned_elements)),
                ("drop_boilerplate", drop_boilerplate),
                ("artifact", context.config.clean_output.name),
            ],
        )

    def _enrich_context(self, context: IngestRunContext) -> None:
        enrich_elements(
            json_path=str(context.config.clean_output),
            context_text_path=str(context.config.context_output),
            worker=self.enrichment_worker,
            model=context.config.enrichment_model,
            cache_config={
                "enabled": True,
                "scope": "ingest_enrichment",
            },
        )

    def _finalize_output(self, context: IngestRunContext) -> None:
        generated_final_output = context.config.clean_output.with_name(f"{context.config.clean_output.stem}_final.json")
        context.generated_final_output = generated_final_output
        if generated_final_output != context.config.final_output:
            context.config.final_output.parent.mkdir(parents=True, exist_ok=True)
            generated_final_output.replace(context.config.final_output)

        if not context.config.final_output.exists():
            raise FileNotFoundError(context.config.final_output)
        print_artifact("final artifact", context.config.final_output)

    def _ingest_vector_db(self, context: IngestRunContext) -> None:
        print_summary("Vector DB ingest", [("source", context.config.final_output.name)])
        retrieval_config = load_retrieval_config()
        indexer = build_ingestion_indexer(retrieval_config)
        result = indexer.ingest_json(str(context.config.final_output))
        print_summary(
            "Vector DB ingest complete",
            [
                ("rows", result["rows"]),
                ("table", result["table_name"]),
                ("created_table", result["created_table"]),
            ],
        )

    def _cleanup_artifacts(self, context: IngestRunContext) -> None:
        cleanup_artifacts(
            context.config.raw_output,
            context.config.clean_output,
            context.config.context_output,
            context.config.final_output,
        )


def _trace_config_snapshot(config: IngestPipelineConfig) -> dict[str, Any]:
    return {
        "raw_output": str(config.raw_output),
        "clean_output": str(config.clean_output),
        "context_output": str(config.context_output),
        "final_output": str(config.final_output),
        "batch_size": config.batch_size,
        "max_workers": config.max_workers,
        "hi_res_model_name": config.hi_res_model_name,
        "min_text_chars": config.min_text_chars,
        "ocr_dpi": config.ocr_dpi,
        "enrichment_provider": config.enrichment_provider,
        "enrichment_model": config.enrichment_model,
        "enrichment_reasoning_effort": config.enrichment_reasoning_effort,
        "registry_provider": config.registry_provider,
        "registry_model": config.registry_model,
        "trace_output_dir": str(config.trace_output_dir),
        "mass_mode": config.mass_mode,
        "sanitize": config.sanitize,
        "min_page_coverage": config.min_page_coverage,
    }


def _context_artifacts(context: IngestRunContext) -> dict[str, str]:
    artifacts = {
        "raw_output": str(context.config.raw_output),
        "clean_output": str(context.config.clean_output),
        "context_output": str(context.config.context_output),
        "final_output": str(context.config.final_output),
    }
    if context.generated_final_output is not None:
        artifacts["generated_final_output"] = str(context.generated_final_output)
    return artifacts


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
    enrichment_provider: str,
    enrichment_model: str,
    enrichment_reasoning_effort: str | None,
    registry_provider: str,
    registry_model: str,
    trace_output_dir: Path,
) -> IngestRunContext:
    config = IngestPipelineConfig(
        raw_output=raw_output,
        clean_output=clean_output,
        context_output=context_output,
        final_output=final_output,
        batch_size=batch_size,
        max_workers=max_workers,
        hi_res_model_name=hi_res_model_name,
        min_text_chars=min_text_chars,
        ocr_dpi=ocr_dpi,
        enrichment_provider=enrichment_provider,
        enrichment_model=enrichment_model,
        enrichment_reasoning_effort=enrichment_reasoning_effort,
        registry_provider=registry_provider,
        registry_model=registry_model,
        trace_output_dir=trace_output_dir,
    )
    runner = IngestPipelineRunner(config)
    trace = IngestTraceRecorder(
        trace_output_dir=config.trace_output_dir,
        mode="single",
        target_path=pdf_path,
        total_documents=1,
        config=_trace_config_snapshot(config),
    )
    try:
        context = runner.run(pdf_path)
    except Exception as exc:
        trace.record_document(
            queue_index=1,
            queue_total=1,
            source_path=pdf_path,
            filename=pdf_path.name,
            status="failed",
            state_trace=[],
            raw_elements=0,
            cleaned_elements=0,
            started_at=datetime.now(timezone.utc).isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=0.0,
            artifacts=_trace_config_snapshot(config),
            error=str(exc),
        )
        print_artifact("trace", trace.trace_path)
        raise

    trace.record_document(
        queue_index=context.queue_index,
        queue_total=context.queue_total,
        source_path=context.pdf_path,
        filename=context.pdf_path.name,
        status="completed",
        state_trace=context.state_trace,
        raw_elements=len(context.raw_elements),
        cleaned_elements=len(context.cleaned_elements),
        started_at=context.started_at,
        completed_at=context.completed_at or datetime.now(timezone.utc).isoformat(),
        duration_seconds=context.duration_seconds,
        artifacts=_context_artifacts(context),
    )
    print_artifact("trace", trace.trace_path)
    return context


def cleanup_artifacts(*paths: Path) -> None:
    seen = set()
    for path in paths:
        normalized = path.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        if path.exists() and path.is_file():
            path.unlink()
            print_artifact("removed", path)


def ensure_queue_directories(root_dir: Path) -> tuple[Path, Path]:
    ingest_dir = root_dir / "to_ingest"
    completed_dir = root_dir / "ingested"
    ingest_dir.mkdir(parents=True, exist_ok=True)
    completed_dir.mkdir(parents=True, exist_ok=True)
    return ingest_dir, completed_dir


def iter_queue_files(ingest_dir: Path) -> list[Path]:
    return sorted(
        [path for path in ingest_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
        key=lambda path: path.name.lower(),
    )


def _unique_destination_path(destination_dir: Path, source_file: Path) -> Path:
    candidate = destination_dir / source_file.name
    if not candidate.exists():
        return candidate

    stem = source_file.stem
    suffix = source_file.suffix
    counter = 1
    while True:
        candidate = destination_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def stage_root_queue_files(root_dir: Path, ingest_dir: Path, completed_dir: Path) -> int:
    staged_count = 0
    for path in sorted(root_dir.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if path.parent in {ingest_dir, completed_dir}:
            continue
        destination = _unique_destination_path(ingest_dir, path)
        shutil.move(str(path), str(destination))
        staged_count += 1
        print_artifact("staged", destination)
    return staged_count


def unique_completed_path(completed_dir: Path, source_file: Path) -> Path:
    return _unique_destination_path(completed_dir, source_file)


def _log_queue_doc_failure(
    context: "IngestRunContext",
    exc: Exception,
    trace: "IngestTraceRecorder",
) -> None:
    """Record a per-doc failure to the trace and log it; does not raise."""
    logger.warning(
        "queue: document failed %s: %s",
        context.pdf_path.name,
        exc,
    )
    try:
        trace.record_document(
            queue_index=context.queue_index,
            queue_total=context.queue_total,
            source_path=context.pdf_path,
            filename=context.pdf_path.name,
            status="failed",
            state_trace=context.state_trace,
            raw_elements=len(context.raw_elements),
            cleaned_elements=len(context.cleaned_elements),
            started_at=context.started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=time.time() - context.start_time,
            artifacts=_context_artifacts(context),
            error=str(exc),
        )
    except Exception as record_exc:
        logger.warning("queue: failed to record trace for %s: %s", context.pdf_path.name, record_exc)
    print_artifact("trace", trace.trace_path)


def run_directory_queue(root_dir: Path, config: IngestPipelineConfig) -> None:
    ingest_dir, completed_dir = ensure_queue_directories(root_dir)
    staged_count = stage_root_queue_files(root_dir, ingest_dir, completed_dir)
    queued_files = iter_queue_files(ingest_dir)

    print_banner(
        "INGEST QUEUE",
        lines=[
            f"Root      : {root_dir}",
            f"To ingest : {ingest_dir}",
            f"Completed : {completed_dir}",
        ],
        char="=",
    )

    if staged_count:
        print_summary("Queue staging", [("staged_from_root", staged_count)])

    if not queued_files:
        print("ℹ️ No PDF files found in the ingest queue.")
        return

    runner = IngestPipelineRunner(config)
    total = len(queued_files)
    print_summary("Queue summary", [("documents", total)])
    trace = IngestTraceRecorder(
        trace_output_dir=config.trace_output_dir,
        mode="queue",
        target_path=root_dir,
        total_documents=total,
        config=_trace_config_snapshot(config),
    )
    for index, pdf_path in enumerate(queued_files, start=1):
        context = IngestRunContext(
            pdf_path=pdf_path,
            config=config,
            queue_index=index,
            queue_total=total,
        )
        try:
            context = runner.run(pdf_path, queue_index=index, queue_total=total)
            destination = unique_completed_path(completed_dir, pdf_path)
            shutil.move(str(pdf_path), str(destination))
            trace.record_document(
                queue_index=context.queue_index,
                queue_total=context.queue_total,
                source_path=context.pdf_path,
                filename=context.pdf_path.name,
                status="completed",
                state_trace=context.state_trace,
                raw_elements=len(context.raw_elements),
                cleaned_elements=len(context.cleaned_elements),
                started_at=context.started_at,
                completed_at=context.completed_at or datetime.now(timezone.utc).isoformat(),
                duration_seconds=context.duration_seconds,
                artifacts=_context_artifacts(context),
                archived_to=destination,
            )
            print_artifact("archived", destination)
        except (LowPageCoverageError, Exception) as exc:
            # Log failure but continue to next document so one bad PDF can't
            # kill the whole queue run.
            _log_queue_doc_failure(context, exc, trace)
            # Attempt to record failure in a standalone audit log entry so the
            # failure trail is always inspectable even without a runner audit.
            try:
                if context.audit is not None:
                    context.audit.record(
                        doc=context.pdf_path.name,
                        phase="queue_error",
                        action="document_failed",
                        inputs=None,
                        chosen=None,
                        confidence=None,
                        rationale=str(exc),
                    )
            except Exception:
                pass
    print_artifact("trace", trace.trace_path)
    print_banner("QUEUE COMPLETE", [f"Processed documents: {total}"], char="=")
