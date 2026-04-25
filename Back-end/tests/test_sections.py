"""Tests for ingestion.stages.sections.attach_sections."""

import json

from ingestion.stages.sections import attach_sections


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_elem(element_type: str, text: str, **extra) -> dict:
    """Build a minimal element dict with type, text, and metadata."""
    elem = {"type": element_type, "text": text, "metadata": {}}
    elem.update(extra)
    return elem


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_input():
    assert attach_sections([]) == []


def test_flat_no_headings_section_path_and_title():
    elements = [
        make_elem("NarrativeText", "Hello world."),
        make_elem("ListItem", "Item one."),
    ]
    result = attach_sections(elements)
    assert len(result) == 2
    for elem in result:
        assert elem["section_path"] == []
        assert elem["section_title"] == ""


def test_flat_no_headings_chunk_type_by_type():
    elements = [
        make_elem("NarrativeText", "Some prose."),
        make_elem("ListItem", "A list item."),
        make_elem("Table", "col1 col2"),
        make_elem("FigureCaption", "Figure 1."),
        make_elem("Footer", "Page 1"),
        make_elem("UncategorizedText", "Unknown text"),
    ]
    result = attach_sections(elements)
    assert result[0]["chunk_type"] == "narrative"
    assert result[1]["chunk_type"] == "list_item"
    assert result[2]["chunk_type"] == "table"
    assert result[3]["chunk_type"] == "caption"
    assert result[4]["chunk_type"] == "uncategorized"
    assert result[5]["chunk_type"] == "uncategorized"


def test_single_title_then_paragraphs():
    elements = [
        make_elem("Title", "Introduction"),
        make_elem("NarrativeText", "First paragraph."),
        make_elem("NarrativeText", "Second paragraph."),
    ]
    result = attach_sections(elements)
    # The title itself
    assert result[0]["section_path"] == ["Introduction"]
    assert result[0]["section_title"] == "Introduction"
    assert result[0]["chunk_type"] == "heading"
    # Paragraphs under the title
    assert result[1]["section_path"] == ["Introduction"]
    assert result[1]["section_title"] == "Introduction"
    assert result[2]["section_path"] == ["Introduction"]


def test_nested_numbered_headings():
    """3 Storage → 3.2 ZFS → 3.2.1 Pool Creation → paragraph."""
    elements = [
        make_elem("Title", "3 Storage"),
        make_elem("Header", "3.2 ZFS"),
        make_elem("Header", "3.2.1 Pool Creation"),
        make_elem("NarrativeText", "Create a pool with zpool create."),
    ]
    result = attach_sections(elements)
    paragraph = result[3]
    assert paragraph["section_path"] == ["3 Storage", "3.2 ZFS", "3.2.1 Pool Creation"]
    assert paragraph["section_title"] == "3.2.1 Pool Creation"


def test_stack_pops_on_sibling_heading():
    """After 3.2.1 Pool Creation, a new 3.3 Networking pops the 3.2.* branch."""
    elements = [
        make_elem("Title", "3 Storage"),
        make_elem("Header", "3.2 ZFS"),
        make_elem("Header", "3.2.1 Pool Creation"),
        make_elem("Header", "3.3 Networking"),
        make_elem("NarrativeText", "Networking overview."),
    ]
    result = attach_sections(elements)
    # 3.3 heading should have path ["3 Storage", "3.3 Networking"]
    heading_3_3 = result[3]
    assert heading_3_3["section_path"] == ["3 Storage", "3.3 Networking"]
    # Paragraph under 3.3
    paragraph = result[4]
    assert paragraph["section_path"] == ["3 Storage", "3.3 Networking"]
    assert paragraph["section_title"] == "3.3 Networking"


def test_new_top_level_heading_resets_stack():
    """A new top-level heading (4 Networking) should reset entirely from 3 Storage."""
    elements = [
        make_elem("Title", "3 Storage"),
        make_elem("Header", "3.2 ZFS"),
        make_elem("Title", "4 Networking"),
        make_elem("NarrativeText", "Networking content."),
    ]
    result = attach_sections(elements)
    # The new Title "4 Networking" should pop everything
    heading_4 = result[2]
    assert heading_4["section_path"] == ["4 Networking"]
    paragraph = result[3]
    assert paragraph["section_path"] == ["4 Networking"]


def test_title_vs_header_depth_fallback():
    """Bare Title (no numbering) → depth 1; bare Header → depth 2."""
    elements = [
        make_elem("Title", "Introduction"),
        make_elem("Header", "Overview"),
        make_elem("NarrativeText", "Some text."),
    ]
    result = attach_sections(elements)
    # Header under Title → stack is [Introduction, Overview]
    assert result[1]["section_path"] == ["Introduction", "Overview"]
    assert result[2]["section_path"] == ["Introduction", "Overview"]


def test_chapter_prefix_treated_as_depth_1():
    """'Chapter 3 Storage' should be depth 1 and reset any existing top-level."""
    elements = [
        make_elem("Title", "Introduction"),
        make_elem("Header", "Background"),
        make_elem("Title", "Chapter 3 Storage"),
        make_elem("NarrativeText", "Storage details."),
    ]
    result = attach_sections(elements)
    # "Chapter 3 Storage" is depth 1, should pop "Introduction" and "Background"
    chapter_heading = result[2]
    assert chapter_heading["section_path"] == ["Chapter 3 Storage"]
    paragraph = result[3]
    assert paragraph["section_path"] == ["Chapter 3 Storage"]


def test_section_prefix_treated_as_depth_1():
    """'Section 4 Summary' should be depth 1."""
    elements = [
        make_elem("Title", "Section 4 Summary"),
        make_elem("NarrativeText", "Summary text."),
    ]
    result = attach_sections(elements)
    assert result[0]["section_path"] == ["Section 4 Summary"]
    assert result[1]["section_path"] == ["Section 4 Summary"]


def test_chunk_type_mapping_all_types():
    """Verify each unstructured element type maps to its expected ChunkType."""
    cases = [
        ("Title", "A Title", "heading"),
        ("Header", "A Header", "heading"),
        ("NarrativeText", "Normal prose without code.", "narrative"),
        ("ListItem", "List item text.", "list_item"),
        ("Table", "col1 | col2", "table"),
        ("FigureCaption", "Figure caption.", "caption"),
        ("Footer", "Page number", "uncategorized"),
        ("UncategorizedText", "Some uncat text.", "uncategorized"),
    ]
    for element_type, text, expected_chunk_type in cases:
        result = attach_sections([make_elem(element_type, text)])
        assert result[0]["chunk_type"] == expected_chunk_type, (
            f"Expected {expected_chunk_type} for type={element_type!r}, "
            f"got {result[0]['chunk_type']!r}"
        )


def test_code_detection_override_narrative():
    """NarrativeText with code-like text is overridden to chunk_type=code."""
    code_texts = [
        "$ sudo apt-get install nginx",
        "#!/bin/bash\necho hello",
        "systemctl restart nginx",
        "mount /dev/sda1 /mnt",
    ]
    for text in code_texts:
        result = attach_sections([make_elem("NarrativeText", text)])
        assert result[0]["chunk_type"] == "code", (
            f"Expected code for text={text!r}, got {result[0]['chunk_type']!r}"
        )


def test_code_detection_override_uncategorized():
    """UncategorizedText that is code-like becomes code."""
    result = attach_sections([make_elem("UncategorizedText", "$ sudo reboot")])
    assert result[0]["chunk_type"] == "code"


def test_empty_text_heading_skipped():
    """An empty-text heading should not affect the stack."""
    elements = [
        make_elem("Title", "Real Section"),
        make_elem("Title", ""),        # empty — skip
        make_elem("NarrativeText", "Content."),
    ]
    result = attach_sections(elements)
    # Stack should still be just ["Real Section"]
    assert result[2]["section_path"] == ["Real Section"]


def test_heading_gets_its_own_path():
    """A heading element itself receives section_path ending at that heading."""
    elements = [make_elem("Title", "Intro")]
    result = attach_sections(elements)
    assert result[0]["section_path"] == ["Intro"]
    assert result[0]["section_title"] == "Intro"
    assert result[0]["chunk_type"] == "heading"


def test_does_not_mutate_input():
    """attach_sections should not modify the original element dicts."""
    original = [
        make_elem("Title", "My Section"),
        make_elem("NarrativeText", "Some text."),
    ]
    # Take copies of the original state
    original_copies = [dict(e) for e in original]
    attach_sections(original)
    for orig, copy in zip(original, original_copies):
        assert orig == copy, "attach_sections mutated the input element"


def test_output_is_json_serializable():
    """chunk_type should be a plain string so json.dumps succeeds."""
    elements = [
        make_elem("Title", "Chapter 1"),
        make_elem("NarrativeText", "Some prose."),
        make_elem("Table", "data"),
    ]
    result = attach_sections(elements)
    # This will raise if any value is a non-serializable enum
    json.dumps(result)


def test_preserves_existing_keys():
    """Extra metadata keys on elements are preserved in the output."""
    elem = {
        "type": "NarrativeText",
        "text": "Hello.",
        "metadata": {"page_number": 3},
        "element_id": "abc123",
        "parent_id": "xyz",
    }
    result = attach_sections([elem])
    out = result[0]
    assert out["element_id"] == "abc123"
    assert out["parent_id"] == "xyz"
    assert out["metadata"]["page_number"] == 3


def test_image_type_uncategorized():
    """Image elements map to uncategorized even when text is code-like."""
    # Non-code alt-text stays uncategorized.
    result = attach_sections([make_elem("Image", "A diagram of the network.")])
    assert result[0]["chunk_type"] == "uncategorized"

    # Code-like alt-text must NOT trigger the code override for Image elements;
    # the override is gated on NarrativeText / UncategorizedText only.
    result_code_like = attach_sections([make_elem("Image", "$ sudo apt install foo")])
    assert result_code_like[0]["chunk_type"] == "uncategorized"


def test_part_prefix_treated_as_depth_1():
    """'Part II Advanced Topics' should be depth 1."""
    elements = [
        make_elem("Title", "Introduction"),
        make_elem("Title", "Part II Advanced Topics"),
        make_elem("NarrativeText", "Advanced content."),
    ]
    result = attach_sections(elements)
    assert result[1]["section_path"] == ["Part II Advanced Topics"]
    assert result[2]["section_path"] == ["Part II Advanced Topics"]
