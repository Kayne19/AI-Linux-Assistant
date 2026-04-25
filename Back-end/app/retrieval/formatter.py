import hashlib
import json
import re
from collections import defaultdict


def normalize_chunk_text(text):
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"-\n(?=\w)", "", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def format_page_label(pages):
    if not pages:
        return "Page ?"
    pages = sorted(set(int(page) for page in pages))
    ranges = []
    start = end = pages[0]
    for page in pages[1:]:
        if page == end + 1:
            end = page
            continue
        ranges.append((start, end))
        start = end = page
    ranges.append((start, end))

    labels = []
    for start, end in ranges:
        if start == end:
            labels.append(str(start))
        else:
            labels.append(f"{start}-{end}")

    if len(labels) == 1 and "-" not in labels[0]:
        return f"Page {labels[0]}"
    return f"Pages {', '.join(labels)}"


def coerce_page_number(value):
    try:
        page = int(value)
    except Exception:
        return None
    return page if page >= 1 else None


def build_row_key(doc):
    explicit_key = doc.get("row_key") or doc.get("id")
    if explicit_key:
        return str(explicit_key)

    source = doc.get("canonical_source_id") or doc.get("source") or "Unknown"
    page = coerce_page_number(doc.get("page_start")) or coerce_page_number(doc.get("page"))
    normalized_text = normalize_chunk_text(doc.get("text", ""))
    digest = hashlib.sha1(
        f"{source}|{page if page is not None else 'unp'}|{normalized_text}".encode("utf-8")
    ).hexdigest()[:12]
    return f"row:{source}:{page if page is not None else 'unp'}:{digest}"


def build_page_window_key(source, page_start, page_end):
    if page_start is None or page_end is None:
        return None
    return f"window:{source}:{int(page_start)}-{int(page_end)}"


def build_bundle_key(source, anchor_row_key, page_start=None, page_end=None):
    if page_start is None or page_end is None:
        return f"bundle:{source}:singleton:{anchor_row_key}"
    return f"bundle:{source}:{int(page_start)}-{int(page_end)}:anchor:{anchor_row_key}"


def build_block_key(source, pages=None, row_keys=None):
    pages = sorted(set(int(page) for page in (pages or [])))
    if pages:
        return f"block:{source}:{pages[0]}-{pages[-1]}"
    row_keys = sorted(str(key) for key in (row_keys or []))
    if row_keys:
        return f"block:{source}:singleton:{row_keys[0]}"
    return f"block:{source}:singleton:unknown"


def _decode_entities(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _doc_source_key(doc):
    return doc.get("source_key") or doc.get("canonical_source_id") or doc.get("source") or "Unknown"


def _doc_display_source(doc):
    return doc.get("canonical_title") or doc.get("source") or _doc_source_key(doc)


def _doc_page_start(doc):
    return coerce_page_number(doc.get("page_start")) or coerce_page_number(doc.get("page"))


def _doc_page_end(doc):
    return coerce_page_number(doc.get("page_end")) or _doc_page_start(doc)


def _finalize_merged_group(source, group_docs, *, bundle_key=None, bundle_rank=0):
    pages = []
    seen_text = set()
    text_blocks = []
    max_score = 0.0
    anchor_doc = {}
    row_keys = []

    for doc in group_docs:
        page_start = _doc_page_start(doc)
        page_end = _doc_page_end(doc)
        if page_start is not None and page_end is not None:
            pages.extend(range(page_start, page_end + 1))

        score = float(doc.get("rerank_score", 0.0))
        if not anchor_doc or score > max_score:
            anchor_doc = doc
            max_score = score
        row_key = build_row_key(doc)
        row_keys.append(row_key)
        normalized = normalize_chunk_text(doc.get("text", ""))
        if not normalized or normalized in seen_text:
            continue
        seen_text.add(normalized)
        text_blocks.append(normalized)

    normalized_pages = sorted(set(pages))
    page_window_key = build_page_window_key(
        source,
        normalized_pages[0] if normalized_pages else None,
        normalized_pages[-1] if normalized_pages else None,
    )

    return {
        "source": _doc_display_source(anchor_doc),
        "source_key": source,
        "pages": normalized_pages,
        "score": max_score,
        "text": "\n\n".join(text_blocks).strip(),
        "bundle_key": bundle_key,
        "bundle_rank": bundle_rank,
        "page_window_key": page_window_key,
        "block_key": build_block_key(source, normalized_pages, row_keys),
        "row_keys": sorted(set(row_keys)),
        "section_path": list(anchor_doc.get("section_path") or []),
        "section_title": anchor_doc.get("section_title") or "",
        "page_start": _doc_page_start(anchor_doc),
        "page_end": _doc_page_end(anchor_doc),
        "chunk_type": anchor_doc.get("chunk_type") or "",
        "local_subsystems": list(anchor_doc.get("local_subsystems") or []),
        "entities": _decode_entities(anchor_doc.get("entities")),
        "canonical_source_id": anchor_doc.get("canonical_source_id") or "",
        "canonical_title": anchor_doc.get("canonical_title") or anchor_doc.get("source") or source,
    }


def merge_context_chunks(docs):
    if not docs:
        return []

    docs_by_bundle = defaultdict(list)
    bundle_rank_by_key = {}
    source_by_bundle = {}
    for index, doc in enumerate(docs):
        source = _doc_source_key(doc)
        bundle_key = doc.get("bundle_key") or build_bundle_key(source, f"legacy-{index}")
        docs_by_bundle[bundle_key].append(doc)
        source_by_bundle[bundle_key] = source
        bundle_rank_by_key[bundle_key] = min(int(doc.get("bundle_rank", index)), bundle_rank_by_key.get(bundle_key, index))

    merged = []
    ordered_bundle_keys = sorted(
        docs_by_bundle.keys(),
        key=lambda key: (bundle_rank_by_key.get(key, 10**9), key),
    )
    for bundle_key in ordered_bundle_keys:
        source = source_by_bundle[bundle_key]
        source_docs = docs_by_bundle[bundle_key]
        sortable = []
        for original_index, doc in enumerate(source_docs):
            page = _doc_page_start(doc)
            sortable.append(
                (
                    page if page is not None else 10**9,
                    original_index,
                    doc,
                )
            )
        sortable.sort()

        current_group = []
        current_last_page = None

        for page_value, _, doc in sortable:
            page = None if page_value == 10**9 else page_value
            if page is None:
                if current_group:
                    merged.append(
                        _finalize_merged_group(
                            source,
                            current_group,
                            bundle_key=bundle_key,
                            bundle_rank=bundle_rank_by_key.get(bundle_key, 0),
                        )
                    )
                    current_group = []
                    current_last_page = None
                merged.append(
                    _finalize_merged_group(
                        source,
                        [doc],
                        bundle_key=bundle_key,
                        bundle_rank=bundle_rank_by_key.get(bundle_key, 0),
                    )
                )
                continue

            contiguous = (
                current_group
                and current_last_page is not None
                and page <= current_last_page + 1
            )

            if current_group and not contiguous:
                merged.append(
                    _finalize_merged_group(
                        source,
                        current_group,
                        bundle_key=bundle_key,
                        bundle_rank=bundle_rank_by_key.get(bundle_key, 0),
                    )
                )
                current_group = []
                current_last_page = None

            current_group.append(doc)
            current_last_page = _doc_page_end(doc) or page

        if current_group:
            merged.append(
                _finalize_merged_group(
                    source,
                    current_group,
                    bundle_key=bundle_key,
                    bundle_rank=bundle_rank_by_key.get(bundle_key, 0),
                )
            )

    merged.sort(
        key=lambda item: (
            item.get("bundle_rank", 10**9),
            item["pages"][0] if item["pages"] else 10**9,
            -item.get("score", 0.0),
        )
    )
    return merged


def format_context_blocks(merged_results):
    blocks = serialize_context_blocks(merged_results)
    context_text = "".join(
        f"---\n[Source: {block['citation_label']}]\n{block['text']}\n"
        for block in blocks
    )
    sources = [block["citation_label"] for block in blocks]
    return context_text, sources


def format_citation_label(block):
    title = block.get("canonical_title") or block.get("source") or "Unknown"
    section_path = [str(part) for part in (block.get("section_path") or []) if part]
    section_label = f" §{' › '.join(section_path)}" if section_path else ""
    page_start = coerce_page_number(block.get("page_start"))
    page_end = coerce_page_number(block.get("page_end"))
    if page_start is None and page_end is None:
        pages = block.get("pages") or []
        if pages:
            page_start = coerce_page_number(pages[0])
            page_end = coerce_page_number(pages[-1])
    if page_start is None and page_end is None:
        return f"{title}{section_label}"
    if page_end is None:
        page_end = page_start
    if page_start is None:
        page_start = page_end
    page_label = f"p.{page_start}" if page_start == page_end else f"p.{page_start}-{page_end}"
    return f"{title}{section_label}, {page_label}"


def serialize_context_blocks(merged_results):
    blocks = []
    for item in merged_results:
        source_file = item["source"]
        pages = sorted(set(int(page) for page in item["pages"]))
        page_label = format_page_label(pages)
        text = item["text"]
        if not text:
            continue
        block = {
            "source": source_file,
            "source_key": item.get("source_key") or source_file,
            "pages": pages,
            "page_label": page_label,
            "text": text,
            "bundle_key": item.get("bundle_key"),
            "block_key": item.get("block_key"),
            "page_window_key": item.get("page_window_key"),
            "section_path": list(item.get("section_path") or []),
            "section_title": item.get("section_title") or "",
            "page_start": item.get("page_start"),
            "page_end": item.get("page_end"),
            "chunk_type": item.get("chunk_type") or "",
            "local_subsystems": list(item.get("local_subsystems") or []),
            "entities": item.get("entities") or {},
            "canonical_source_id": item.get("canonical_source_id") or "",
            "canonical_title": item.get("canonical_title") or source_file,
        }
        block["citation_label"] = format_citation_label(block)
        blocks.append(block)
    return blocks
