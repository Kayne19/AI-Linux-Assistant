from retrieval.formatter import format_page_label


def _coerce_text(value):
    return str(value or "")


def normalize_recent_turns(items):
    normalized = []
    for item in items or []:
        role = ""
        content = ""
        if isinstance(item, tuple) and len(item) == 2:
            role, content = item
        elif isinstance(item, dict):
            role = item.get("role", "")
            content = item.get("content") or item.get("parts", [{}])[0].get("text", "")
        role = _coerce_text(role).strip()
        content = _coerce_text(content).strip()
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def normalize_retrieved_context_blocks(blocks):
    normalized = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        source = _coerce_text(block.get("source") or "Unknown").strip() or "Unknown"
        pages = []
        for page in block.get("pages") or []:
            try:
                pages.append(int(page))
            except (TypeError, ValueError):
                continue
        pages = sorted(set(pages))
        page_label = _coerce_text(block.get("page_label") or format_page_label(pages)).strip() or format_page_label(pages)
        text = _coerce_text(block.get("text")).strip()
        if not text:
            continue
        normalized.append(
            {
                "source": source,
                "pages": pages,
                "page_label": page_label,
                "text": text,
            }
        )
    return normalized


def context_text_from_blocks(blocks):
    sections = []
    for block in normalize_retrieved_context_blocks(blocks):
        sections.append(f"---\n[Source: {block['source']} ({block['page_label']})]\n{block['text']}\n")
    return "".join(sections)


def empty_normalized_inputs(request_text=""):
    return {
        "request_text": _coerce_text(request_text),
        "conversation_summary_text": "",
        "recent_turns": [],
        "memory_snapshot_text": "",
        "retrieval_query": "",
        "retrieved_context_text": "",
        "retrieved_context_blocks": [],
    }


def normalize_saved_normalized_inputs(value, request_text=""):
    normalized = empty_normalized_inputs(request_text=request_text)
    if not isinstance(value, dict):
        return normalized

    if "request_text" in value:
        normalized["request_text"] = _coerce_text(value.get("request_text"))
    normalized["conversation_summary_text"] = _coerce_text(value.get("conversation_summary_text")).strip()
    normalized["recent_turns"] = normalize_recent_turns(value.get("recent_turns"))
    normalized["memory_snapshot_text"] = _coerce_text(value.get("memory_snapshot_text")).strip()
    normalized["retrieval_query"] = _coerce_text(value.get("retrieval_query")).strip()
    normalized["retrieved_context_blocks"] = normalize_retrieved_context_blocks(value.get("retrieved_context_blocks"))
    normalized["retrieved_context_text"] = _coerce_text(value.get("retrieved_context_text")).strip()
    if not normalized["retrieved_context_text"] and normalized["retrieved_context_blocks"]:
        normalized["retrieved_context_text"] = context_text_from_blocks(normalized["retrieved_context_blocks"]).strip()
    return normalized


def build_normalized_inputs(
    *,
    request_text="",
    summarized_conversation_history=None,
    memory_snapshot_text="",
    retrieval_query="",
    retrieved_docs="",
    retrieved_context_blocks=None,
):
    return normalize_saved_normalized_inputs(
        {
            "request_text": request_text,
            "conversation_summary_text": getattr(summarized_conversation_history, "summary_text", "") or "",
            "recent_turns": list(getattr(summarized_conversation_history, "recent_turns", []) or []),
            "memory_snapshot_text": memory_snapshot_text,
            "retrieval_query": retrieval_query,
            "retrieved_context_text": retrieved_docs,
            "retrieved_context_blocks": retrieved_context_blocks or [],
        },
        request_text=request_text,
    )
