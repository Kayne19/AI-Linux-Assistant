"""Section hierarchy detection stage for the ingestion pipeline.

Walks cleaned elements in reading order, maintains a heading stack keyed by
numeric depth, and attaches ``section_path``, ``section_title``, and
``chunk_type`` to every element.
"""

import re
from typing import Any

from ingestion.identity.vocabularies import ChunkType
from ingestion.stages.cleaner import is_code_like

# ---------------------------------------------------------------------------
# Heading depth inference
# ---------------------------------------------------------------------------

# Matches "3.2.1 Title" style multi-level numbering at line start.
_MULTI_LEVEL_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)\b")

# Matches "3. Title" style single top-level numbering (digit then period then
# whitespace then a non-whitespace character).
_TOP_LEVEL_NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s+\S")

# Matches bare Chapter/Part/Section prefixes (always depth 1).
_CHAPTER_PREFIX_RE = re.compile(
    r"^\s*(chapter|part|section)\s+", re.IGNORECASE
)


def _infer_heading_depth(text: str, element_type: str) -> int:
    """Return the numeric depth (1 = top level) for a heading element."""
    stripped = text.strip()

    # Multi-level numbering: 3.2.1 → depth 3
    match = _MULTI_LEVEL_RE.match(stripped)
    if match:
        parts = match.group(1).split(".")
        return len(parts)

    # Chapter/Part/Section prefix → depth 1
    if _CHAPTER_PREFIX_RE.match(stripped):
        return 1

    # Single top-level numbered heading: "3. Foo" → depth 1
    if _TOP_LEVEL_NUMBERED_RE.match(stripped):
        return 1

    # Fallback by element type
    if element_type == "Title":
        return 1
    # Header fallback → depth 2
    return 2


# ---------------------------------------------------------------------------
# chunk_type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, ChunkType] = {
    "Title": ChunkType.heading,
    "Header": ChunkType.heading,
    "NarrativeText": ChunkType.narrative,
    "ListItem": ChunkType.list_item,
    "Table": ChunkType.table,
    "FigureCaption": ChunkType.caption,
    "UncategorizedText": ChunkType.uncategorized,
    "Image": ChunkType.uncategorized,
    "Footer": ChunkType.uncategorized,
}

# Only text elements whose original type is NarrativeText or UncategorizedText
# are candidates for the code-detection override.  Image (and other non-text
# types) keep their mapped value regardless of text content.
_CODE_OVERRIDE_ELEMENT_TYPES = {"NarrativeText", "UncategorizedText"}


def _map_chunk_type(element_type: str, text: str) -> ChunkType:
    """Return the ChunkType for the element, with code-detection override."""
    chunk_type = _TYPE_MAP.get(element_type, ChunkType.uncategorized)
    if element_type in _CODE_OVERRIDE_ELEMENT_TYPES and is_code_like(text):
        return ChunkType.code
    return chunk_type


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_HEADING_TYPES = {"Title", "Header"}


def attach_sections(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk elements in reading order, attach section_path/section_title/chunk_type.

    Returns a new list (does not mutate input). Heading elements themselves
    receive section_path (the path *ending* at that heading) and
    chunk_type="heading". Non-heading elements receive the path of the most
    recent heading stack.
    """
    # Stack of (depth: int, title: str) tuples, monotonically increasing depth.
    stack: list[tuple[int, str]] = []

    result: list[dict[str, Any]] = []

    for element in elements:
        element_type = element.get("type", "UncategorizedText")
        text = element.get("text", "") or ""
        chunk_type = _map_chunk_type(element_type, text)

        if element_type in _HEADING_TYPES:
            if text.strip():
                # Compute depth before pushing
                depth = _infer_heading_depth(text, element_type)
                # Pop any frames with depth >= d to maintain monotonic stack;
                # push after so the heading's own path ends at itself.
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, text.strip()))

        section_path = [title for (_, title) in stack]

        section_title = section_path[-1] if section_path else ""

        new_element = dict(element)
        new_element["section_path"] = section_path
        new_element["section_title"] = section_title
        new_element["chunk_type"] = chunk_type.value
        result.append(new_element)

    return result
