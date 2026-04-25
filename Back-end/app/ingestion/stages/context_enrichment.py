"""Context enrichment stage.

Split into four reusable pieces so the same chunk payloads can run in either
mode:

- ``build_enrichment_requests`` — pure builder; produces the list of per-chunk
  :class:`EnrichmentRequest` records (system prompt, user message, model,
  limits) for every eligible element.
- ``enrich_sync`` — sequential executor that drives each request through a
  provider worker with prompt caching. Used by the ``--sync`` fast-path.
- ``enrich_batch_prepare`` / ``enrich_batch_submit`` / ``enrich_batch_poll`` —
  thin helpers that serialize requests to the OpenAI Batch JSONL format and
  manage a batch job through the transport wrapper in
  ``providers/openai_batch.py``.
- ``enrich_batch_merge`` — reads a downloaded Batch result file, matches rows
  back to their source elements by ``custom_id``, and merges ``ai_context``
  into the element metadata.

``enrich_elements`` is kept as a legacy, backward-compatible entry point that
composes ``build_enrichment_requests`` + ``enrich_sync`` and writes the
``_final.json`` artifact expected by the rest of the pipeline.
"""

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.console import print_artifact, print_progress, print_state, print_summary

TARGET_ELEMENT_TYPES = ("NarrativeText", "ListItem", "UncategorizedText")
MIN_CHUNK_CHARS = 50
DOC_CONTEXT_TRUNCATE = 25000


@dataclass(frozen=True)
class EnrichmentRequest:
    """A single per-chunk enrichment request.

    ``custom_id`` correlates responses back to the originating element in
    both the sync and batch paths. ``element_index`` is the position in the
    elements list used by the builder; the batch-merge path resolves via
    ``custom_id`` so callers can persist the requests list between runs.
    """

    custom_id: str
    element_index: int
    system_prompt: str
    user_message: str
    model: str
    temperature: float = 0.1
    max_output_tokens: int = 120


@dataclass
class EnrichmentResult:
    """Aggregate outcome of a sync run or batch merge."""

    completed_count: int = 0
    error_count: int = 0
    cache_metrics: dict = field(
        default_factory=lambda: {"cached_tokens": 0, "input_tokens": 0, "output_tokens": 0}
    )
    errors: list = field(default_factory=list)


def _build_document_cache_key(full_doc_context: str) -> str:
    return hashlib.sha256(full_doc_context[:DOC_CONTEXT_TRUNCATE].encode("utf-8")).hexdigest()[:16]


def _build_system_prompt(full_doc_context: str) -> str:
    return f"""
    <document_context>
    {full_doc_context[:DOC_CONTEXT_TRUNCATE]}
    ... (truncated)
    </document_context>

    You are a retrieval optimizer. Your job is to situate chunks within the document context above.
    For each request, you will receive exactly one chunk.
    Return exactly one short, succinct sentence situating that chunk within the document context.
    Do not use bullet points.
    Do not quote large spans verbatim.
    Do not mention that the chunk was "provided" or "shown".
    """


def _build_user_message(text_content: str) -> str:
    return f"""
    <chunk>
    {text_content}
    </chunk>
    """


def _custom_id_for(element_index: int, text_content: str) -> str:
    digest = hashlib.sha256(text_content.encode("utf-8")).hexdigest()[:12]
    return f"chunk-{element_index:06d}-{digest}"


def _apply_enrichment(element: dict, context_text: str) -> None:
    if "metadata" not in element:
        element["metadata"] = {}
    element["metadata"]["ai_context"] = context_text
    original_text = element.get("text", "")
    element["metadata"]["embedding_text"] = f"CONTEXT: {context_text}\n\nCONTENT: {original_text}"


def build_enrichment_requests(
    elements,
    full_doc_context,
    *,
    model,
    target_types=TARGET_ELEMENT_TYPES,
    min_chars=MIN_CHUNK_CHARS,
    temperature: float = 0.1,
    max_output_tokens: int = 120,
):
    """Return an :class:`EnrichmentRequest` per eligible element.

    Filtering matches the legacy behavior: only the configured
    ``target_types`` whose ``text`` is strictly longer than ``min_chars``.
    The document-scoped system prompt is built once and shared across all
    requests so downstream prompt caching keeps a single cached prefix.
    """
    system_prompt = _build_system_prompt(full_doc_context)
    requests: list[EnrichmentRequest] = []
    for index, element in enumerate(elements):
        if element.get("type") not in target_types:
            continue
        text = element.get("text", "")
        if len(text) <= min_chars:
            continue
        requests.append(
            EnrichmentRequest(
                custom_id=_custom_id_for(index, text),
                element_index=index,
                system_prompt=system_prompt,
                user_message=_build_user_message(text),
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
        )
    return requests


def enrich_sync(
    requests,
    elements,
    worker,
    *,
    full_doc_context=None,
    cache_config=None,
    event_listener=None,
    progress_label: str = "enrich chunks",
    on_partial_save=None,
):
    """Execute *requests* sequentially against *worker*.

    Each successful response mutates ``elements[request.element_index]`` with
    ``ai_context`` + ``embedding_text``. Prompt-cache metrics are aggregated
    into the returned :class:`EnrichmentResult`.

    ``on_partial_save`` is an optional ``callable(index, elements)`` hook the
    caller can use to persist intermediate state (the legacy pipeline saves
    every 100 chunks).
    """
    if full_doc_context is not None:
        effective_cache_config = {
            "enabled": True,
            "scope": "ingest_enrichment",
            "key_suffix": _build_document_cache_key(full_doc_context),
        }
    else:
        effective_cache_config = {"enabled": True, "scope": "ingest_enrichment"}
    if cache_config:
        effective_cache_config.update(cache_config)

    result = EnrichmentResult()

    def _listener(event_type, payload):
        if event_type == "prompt_cache_metrics":
            result.cache_metrics["cached_tokens"] += int(payload.get("cached_tokens") or 0)
            result.cache_metrics["input_tokens"] += int(payload.get("input_tokens") or 0)
            result.cache_metrics["output_tokens"] += int(payload.get("output_tokens") or 0)
        if event_listener is not None:
            event_listener(event_type, payload)

    total = len(requests)
    for index, request in enumerate(requests, start=1):
        try:
            context_text = worker.generate_text(
                system_prompt=request.system_prompt,
                user_message=request.user_message,
                history=[],
                temperature=request.temperature,
                max_output_tokens=request.max_output_tokens,
                event_listener=_listener,
                cache_config=effective_cache_config,
            ).strip()
            _apply_enrichment(elements[request.element_index], context_text)
            result.completed_count += 1

            if index == 1 or index == total or index % 25 == 0:
                print_progress(progress_label, index, total)
            if on_partial_save is not None and index > 0 and index % 100 == 0:
                on_partial_save(index, elements)
        except KeyboardInterrupt:
            print("\nenrichment interrupted • saving current progress")
            break
        except Exception as exc:
            result.error_count += 1
            result.errors.append({"element_index": request.element_index, "error": str(exc)})
            print(f"enrichment warning • chunk={index} error={exc}")
            continue

    return result


def enrich_batch_prepare(requests, out_path):
    """Serialize *requests* to OpenAI Batch JSONL at *out_path*.

    Each line is the Batch API envelope for ``/v1/responses``::

        {"custom_id": "...", "method": "POST", "url": "/v1/responses", "body": {...}}
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for request in requests:
            envelope = {
                "custom_id": request.custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": request.model,
                    "instructions": request.system_prompt,
                    "input": [{"role": "user", "content": request.user_message}],
                    "temperature": request.temperature,
                    "max_output_tokens": request.max_output_tokens,
                },
            }
            handle.write(json.dumps(envelope))
            handle.write("\n")
    return out_path


def enrich_batch_submit(jsonl_path, *, client=None, metadata=None):
    """Upload *jsonl_path* and create a batch job.

    Returns a :class:`providers.openai_batch.BatchSubmission`. A caller can
    inject ``client`` for tests; otherwise a default
    :class:`OpenAIBatchClient` is constructed from environment variables.
    """
    from providers.openai_batch import OpenAIBatchClient

    client = client or OpenAIBatchClient()
    file_id = client.upload_jsonl(Path(jsonl_path))
    return client.submit_batch(file_id, metadata=metadata)


def enrich_batch_poll(batch_id, *, client=None):
    """Return current :class:`BatchStatus` for *batch_id*."""
    from providers.openai_batch import OpenAIBatchClient

    client = client or OpenAIBatchClient()
    return client.get_status(batch_id)


def _extract_output_text_from_batch_line(record: dict) -> str:
    """Pull the assistant text from a ``/v1/responses`` batch result row."""
    response = record.get("response") or {}
    body = response.get("body") or {}
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    collected: list[str] = []
    for item in body.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                collected.append(part["text"])
    return "".join(collected)


def enrich_batch_merge(requests, results_path, elements):
    """Merge downloaded Batch API output into *elements*.

    Args:
        requests: The :class:`EnrichmentRequest` list produced by
            ``build_enrichment_requests``. Used to resolve ``custom_id`` →
            ``element_index``.
        results_path: Path to the downloaded Batch output JSONL.
        elements: The element list to mutate in place.

    Returns:
        An :class:`EnrichmentResult` aggregating completed/error counts and
        token usage (input, output, cached).
    """
    by_custom_id = {r.custom_id: r for r in requests}
    result = EnrichmentResult()
    path = Path(results_path)
    if not path.exists():
        return result

    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                result.error_count += 1
                continue
            custom_id = record.get("custom_id")
            request = by_custom_id.get(custom_id)
            if request is None:
                # Don't silently drop rows whose custom_id we can't match —
                # mismatch is signal that the prepare/merge contract drifted
                # or that the result file is for a different doc.
                result.error_count += 1
                result.errors.append(
                    {"custom_id": custom_id, "error": "unmatched custom_id"}
                )
                continue
            if record.get("error"):
                result.error_count += 1
                result.errors.append({"custom_id": custom_id, "error": record["error"]})
                continue
            text = _extract_output_text_from_batch_line(record).strip()
            if not text:
                result.error_count += 1
                result.errors.append({"custom_id": custom_id, "error": "empty output"})
                continue
            _apply_enrichment(elements[request.element_index], text)
            result.completed_count += 1

            usage = ((record.get("response") or {}).get("body") or {}).get("usage") or {}
            result.cache_metrics["input_tokens"] += int(usage.get("input_tokens") or 0)
            result.cache_metrics["output_tokens"] += int(usage.get("output_tokens") or 0)
            input_details = usage.get("input_tokens_details") or {}
            result.cache_metrics["cached_tokens"] += int(input_details.get("cached_tokens") or 0)
    return result


def enrich_elements(
    json_path,
    context_text_path,
    worker,
    model="gpt-5.4-nano",
    cache_config=None,
):
    """Legacy sync fast-path.

    Reads elements and context from disk, builds requests, drives them
    through ``enrich_sync``, and writes the enriched result to
    ``<stem>_final.json``. Preserved for pipeline.py and the CLI script.
    """
    with open(json_path, "r", encoding="utf-8") as handle:
        elements = json.load(handle)
    with open(context_text_path, "r", encoding="utf-8") as handle:
        full_doc_context = handle.read()

    requests = build_enrichment_requests(elements, full_doc_context, model=model)

    print_state("🧠 ENRICHMENT_INPUT", Path(json_path).name)
    print_summary(
        "Enrichment preparation",
        [
            ("elements", len(elements)),
            ("candidates", len(requests)),
            ("model", model),
        ],
    )

    def _partial_save(_index, current_elements):
        partial_path = json_path.replace(".json", "_enriched_partial.json")
        with open(partial_path, "w", encoding="utf-8") as handle:
            json.dump(current_elements, handle)
        print_artifact("partial save", Path(partial_path))

    result = enrich_sync(
        requests,
        elements,
        worker,
        full_doc_context=full_doc_context,
        cache_config=cache_config,
        on_partial_save=_partial_save,
    )

    final_path = json_path.replace(".json", "_final.json")
    with open(final_path, "w", encoding="utf-8") as handle:
        json.dump(elements, handle, indent=2)

    print_summary(
        "Enrichment complete",
        [
            ("completed", result.completed_count),
            ("errors", result.error_count),
            ("cached_tokens", result.cache_metrics["cached_tokens"]),
            ("final_output", Path(final_path).name),
        ],
    )
    print_artifact("final output", Path(final_path))
    return result


if __name__ == "__main__":
    raise SystemExit(
        "Run this stage via scripts/ingest/context_enrichment.py or the full ingest pipeline."
    )
