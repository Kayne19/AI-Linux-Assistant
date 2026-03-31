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


def _finalize_merged_group(source, group_docs):
    pages = []
    seen_text = set()
    text_blocks = []
    max_score = 0.0

    for doc in group_docs:
        try:
            pages.append(int(doc.get("page")))
        except Exception:
            pass

        max_score = max(max_score, float(doc.get("rerank_score", 0.0)))
        normalized = normalize_chunk_text(doc.get("text", ""))
        if not normalized or normalized in seen_text:
            continue
        seen_text.add(normalized)
        text_blocks.append(normalized)

    return {
        "source": source,
        "pages": sorted(set(pages)),
        "score": max_score,
        "text": "\n\n".join(text_blocks).strip(),
    }


def merge_context_chunks(docs):
    if not docs:
        return []

    docs_by_source = defaultdict(list)
    for doc in docs:
        source = doc.get("source", "Unknown")
        docs_by_source[source].append(doc)

    merged = []
    for source, source_docs in docs_by_source.items():
        sortable = []
        for original_index, doc in enumerate(source_docs):
            try:
                page = int(doc.get("page"))
            except Exception:
                page = None
            sortable.append(
                (
                    page if page is not None else 10**9,
                    -float(doc.get("rerank_score", 0.0)),
                    original_index,
                    doc,
                )
            )
        sortable.sort()

        current_group = []
        current_last_page = None
        current_start_page = None

        for page_value, _, _, doc in sortable:
            page = None if page_value == 10**9 else page_value
            contiguous = (
                current_group
                and page is not None
                and current_last_page is not None
                and page <= current_last_page + 1
                and current_start_page is not None
                and page <= current_start_page + 1
            )

            if current_group and not contiguous:
                merged.append(_finalize_merged_group(source, current_group))
                current_group = []
                current_last_page = None
                current_start_page = None

            current_group.append(doc)
            if page is not None:
                if current_start_page is None:
                    current_start_page = page
                current_last_page = page

        if current_group:
            merged.append(_finalize_merged_group(source, current_group))

    merged.sort(key=lambda item: item["score"], reverse=True)
    return merged


def format_context_blocks(merged_results):
    context_text = ""
    sources = []
    for item in merged_results:
        source_file = item["source"]
        page_label = format_page_label(item["pages"])
        text = item["text"]
        if not text:
            continue

        context_text += f"---\n[Source: {source_file} ({page_label})]\n{text}\n"
        sources.append(f"{source_file}:{page_label}")
    return context_text, sources
