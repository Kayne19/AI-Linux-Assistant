from retrieval.formatter import (
    _decode_entities,
    format_citation_label,
    merge_context_chunks,
    serialize_context_blocks,
)


def test_format_citation_label_uses_section_path_and_page_range():
    label = format_citation_label(
        {
            "canonical_title": "Proxmox VE Admin Guide",
            "section_path": ["3 Storage", "3.2 ZFS", "3.2.1 ZFS Pool Creation"],
            "page_start": 47,
            "page_end": 49,
        }
    )

    assert label == "Proxmox VE Admin Guide §3 Storage › 3.2 ZFS › 3.2.1 ZFS Pool Creation, p.47-49"


def test_format_citation_label_renders_single_page():
    label = format_citation_label(
        {
            "canonical_title": "Proxmox VE Admin Guide",
            "section_path": ["3.2.1 ZFS Pool Creation"],
            "page_start": 47,
            "page_end": 47,
        }
    )

    assert label == "Proxmox VE Admin Guide §3.2.1 ZFS Pool Creation, p.47"


def test_format_citation_label_renders_single_section_without_separator():
    label = format_citation_label(
        {
            "canonical_title": "Debian Administrator's Handbook",
            "section_path": ["APT"],
            "page_start": 12,
            "page_end": 14,
        }
    )

    assert label == "Debian Administrator's Handbook §APT, p.12-14"


def test_serialize_context_blocks_round_trips_entities_json_string():
    docs = [
        {
            "id": "vec_1",
            "source": "Proxmox.pdf",
            "page": 47,
            "text": "Create the pool.",
            "rerank_score": 9.0,
            "section_path": ["3 Storage", "3.2 ZFS"],
            "section_title": "ZFS",
            "page_start": 47,
            "page_end": 49,
            "chunk_type": "procedure",
            "local_subsystems": ["filesystems"],
            "entities": '{"commands": ["zpool"]}',
            "canonical_source_id": "proxmox-ve-8-admin-guide",
            "canonical_title": "Proxmox VE Admin Guide",
        }
    ]

    blocks = serialize_context_blocks(merge_context_chunks(docs))

    assert blocks[0]["entities"] == {"commands": ["zpool"]}
    assert blocks[0]["section_path"] == ["3 Storage", "3.2 ZFS"]
    assert blocks[0]["chunk_type"] == "procedure"
    assert blocks[0]["local_subsystems"] == ["filesystems"]
    assert blocks[0]["canonical_source_id"] == "proxmox-ve-8-admin-guide"
    assert blocks[0]["citation_label"] == "Proxmox VE Admin Guide §3 Storage › 3.2 ZFS, p.47-49"


def test_decode_entities_handles_bad_json_gracefully():
    assert _decode_entities("{bad json") == {}
