import json
import hashlib
from pathlib import Path

from ingestion.console import print_artifact, print_progress, print_state, print_summary


def _build_document_cache_key(full_doc_context: str) -> str:
    return hashlib.sha256(full_doc_context[:25000].encode("utf-8")).hexdigest()[:16]


def enrich_elements(
    json_path,
    context_text_path,
    worker,
    model="gpt-5.4-nano",
    cache_config=None,
):
    with open(json_path, "r", encoding="utf-8") as handle:
        elements = json.load(handle)

    with open(context_text_path, "r", encoding="utf-8") as handle:
        full_doc_context = handle.read()

    target_types = ["NarrativeText", "ListItem", "UncategorizedText"]
    candidates = [
        element for element in elements if element.get("type") in target_types and len(element.get("text", "")) > 50
    ]

    print_state("🧠 ENRICHMENT_INPUT", Path(json_path).name)
    print_summary(
        "Enrichment preparation",
        [
            ("elements", len(elements)),
            ("candidates", len(candidates)),
            ("model", model),
        ],
    )

    # Keep the cache key document-scoped so every chunk call for the same
    # document reuses the same large cached prefix (system prompt + doc context).
    effective_cache_config = {
        "enabled": True,
        "scope": "ingest_enrichment",
        "key_suffix": _build_document_cache_key(full_doc_context),
    }
    if cache_config:
        effective_cache_config.update(cache_config)

    system_msg = f"""
    <document_context>
    {full_doc_context[:25000]}
    ... (truncated)
    </document_context>

    You are a retrieval optimizer. Your job is to situate chunks within the document context above.
    For each request, you will receive exactly one chunk.
    Return exactly one short, succinct sentence situating that chunk within the document context.
    Do not use bullet points.
    Do not quote large spans verbatim.
    Do not mention that the chunk was "provided" or "shown".
    """

    error_count = 0
    completed_count = 0
    cache_metrics = {
        "cached_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    def _handle_worker_event(event_type, payload):
        if event_type != "prompt_cache_metrics":
            return
        cache_metrics["cached_tokens"] += int(payload.get("cached_tokens") or 0)
        cache_metrics["input_tokens"] += int(payload.get("input_tokens") or 0)
        cache_metrics["output_tokens"] += int(payload.get("output_tokens") or 0)

    total_candidates = len(candidates)
    for index, element in enumerate(candidates, start=1):
        text_content = element.get("text", "")

        user_msg = f"""
        <chunk>
        {text_content}
        </chunk>
        """

        try:
            context = worker.generate_text(
                system_prompt=system_msg,
                user_message=user_msg,
                history=[],
                temperature=0.1,
                max_output_tokens=120,
                event_listener=_handle_worker_event,
                cache_config=effective_cache_config,
            )
            context = context.strip()

            if "metadata" not in element:
                element["metadata"] = {}

            element["metadata"]["ai_context"] = context
            element["metadata"]["embedding_text"] = f"CONTEXT: {context}\n\nCONTENT: {text_content}"
            completed_count += 1

            if index == 1 or index == total_candidates or index % 25 == 0:
                print_progress("enrich chunks", index, total_candidates)

            if index > 0 and index % 100 == 0:
                partial_path = json_path.replace(".json", "_enriched_partial.json")
                with open(partial_path, "w", encoding="utf-8") as handle:
                    json.dump(elements, handle)
                print_artifact("partial save", Path(partial_path))

        except KeyboardInterrupt:
            print("\nenrichment interrupted • saving current progress")
            break
        except Exception as exc:
            error_count += 1
            print(f"enrichment warning • chunk={index} error={exc}")
            continue

    final_path = json_path.replace(".json", "_final.json")
    with open(final_path, "w", encoding="utf-8") as handle:
        json.dump(elements, handle, indent=2)

    print_summary(
        "Enrichment complete",
        [
            ("completed", completed_count),
            ("errors", error_count),
            ("cached_tokens", cache_metrics["cached_tokens"]),
            ("final_output", Path(final_path).name),
        ],
    )
    print_artifact("final output", Path(final_path))


if __name__ == "__main__":
    raise SystemExit(
        "Run this stage via scripts/ingest/context_enrichment.py or the full ingest pipeline."
    )
