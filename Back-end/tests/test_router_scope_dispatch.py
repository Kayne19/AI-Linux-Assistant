from orchestration.model_router import ModelRouter


class ScopedDatabase:
    def __init__(self, known_ids=None):
        self.known_ids = list(known_ids or ["proxmox-ve-8-admin-guide"])
        self.calls = []

    def known_canonical_doc_ids(self):
        return list(self.known_ids)

    def retrieve_context_result(
        self,
        query,
        sources,
        excluded_page_windows=None,
        excluded_block_keys=None,
        covered_region_keys=None,
        requested_evidence_goal=None,
        router_hint=None,
        explicit_doc_ids=(),
    ):
        self.calls.append(
            {
                "query": query,
                "sources": tuple(sources or ()),
                "excluded_page_windows": list(excluded_page_windows or ()),
                "excluded_block_keys": list(excluded_block_keys or ()),
                "covered_region_keys": set(covered_region_keys or ()),
                "requested_evidence_goal": requested_evidence_goal,
                "router_hint": router_hint,
                "explicit_doc_ids": tuple(explicit_doc_ids or ()),
            }
        )
        return {
            "context_text": "scoped docs",
            "selected_sources": [],
            "merged_blocks": [],
            "bundle_summaries": [],
            "retrieval_metadata": {
                "anchor_count": 1,
                "anchor_pages": [],
                "fetched_neighbor_pages": [],
                "delivered_bundle_count": 0,
                "delivered_bundle_keys": [],
                "delivered_block_keys": [],
                "delivered_page_window_keys": [],
                "delivered_page_windows": [],
                "excluded_seen_count": 0,
                "skipped_bundle_count": 0,
            },
        }


class DummyAgent:
    def call_api(self, *args, **kwargs):
        del args, kwargs
        return ""


class DummyWorker:
    def generate_text(self, *args, **kwargs):
        del args, kwargs
        return ""


def build_router(database=None):
    return ModelRouter(
        database=database or ScopedDatabase(),
        classifier=DummyAgent(),
        context_agent=DummyAgent(),
        history_summarizer=DummyAgent(),
        context_summarizer=DummyAgent(),
        responder=DummyAgent(),
        chat_namer=DummyWorker(),
    )


def test_scope_hints_reach_direct_responder_tool_retrieval():
    database = ScopedDatabase()
    router = build_router(database)

    result = router._handle_responder_tool_call(
        "search_rag_database",
        {
            "query": "install with apt",
            "relevant_documents": ["debian"],
            "scope_hints": {"os_family": "linux", "package_managers": ["apt"]},
        },
    )

    assert result == "scoped docs"
    assert database.calls[0]["router_hint"] == {
        "os_family": "linux",
        "package_managers": ["apt"],
    }


def test_canonical_source_ids_reach_direct_responder_tool_retrieval():
    database = ScopedDatabase(known_ids=["proxmox-ve-8-admin-guide", "debian-install-guide-12"])
    router = build_router(database)

    router._handle_responder_tool_call(
        "search_rag_database",
        {
            "query": "create zfs pool",
            "relevant_documents": ["proxmox"],
            "canonical_source_ids": ["proxmox-ve-8-admin-guide"],
        },
    )

    assert database.calls[0]["explicit_doc_ids"] == ("proxmox-ve-8-admin-guide",)


def test_invalid_scope_hints_and_unknown_doc_ids_are_filtered():
    database = ScopedDatabase(known_ids=["proxmox-ve-8-admin-guide"])
    router = build_router(database)

    router._handle_responder_tool_call(
        "search_rag_database",
        {
            "query": "create zfs pool",
            "relevant_documents": ["proxmox"],
            "scope_hints": {
                "os_family": "frobnicator",
                "package_managers": ["bogus", "apt"],
            },
            "canonical_source_ids": ["bogus-id"],
        },
    )

    assert database.calls[0]["router_hint"] == {"package_managers": ["apt"]}
    assert database.calls[0]["explicit_doc_ids"] == ()


def test_scope_hints_change_fingerprint_cache_key_for_same_query():
    database = ScopedDatabase()
    router = build_router(database)

    router._handle_responder_tool_call(
        "search_rag_database",
        {
            "query": "same query",
            "relevant_documents": ["debian"],
            "scope_hints": {"package_managers": ["apt"]},
        },
    )
    router._handle_responder_tool_call(
        "search_rag_database",
        {
            "query": "same query",
            "relevant_documents": ["debian"],
            "scope_hints": {"init_systems": ["systemd"]},
        },
    )

    assert len(database.calls) == 2
    assert database.calls[0]["router_hint"] == {"package_managers": ["apt"]}
    assert database.calls[1]["router_hint"] == {"init_systems": ["systemd"]}


def test_regular_responder_decision_search_carries_scope_fields():
    database = ScopedDatabase(known_ids=["proxmox-ve-8-admin-guide"])
    router = build_router(database)
    decision = {
        "query": "create zfs pool",
        "relevant_documents": ["proxmox"],
        "requested_evidence_goal": "configure_access",
        "repeat_reason": "",
        "gap_type": "procedural_doc_gap",
        "unresolved_gap": "need exact Proxmox storage steps",
        "scope_hints": {"source_family": "proxmox", "major_subsystems": ["storage"]},
        "canonical_source_ids": ("proxmox-ve-8-admin-guide",),
    }

    payload, _metadata = router._execute_regular_responder_search(decision)

    assert payload["search_result_text"] == "scoped docs"
    assert database.calls[0]["router_hint"] == {
        "source_family": "proxmox",
        "major_subsystems": ["storage"],
    }
    assert database.calls[0]["explicit_doc_ids"] == ("proxmox-ve-8-admin-guide",)


def test_tool_schema_reads_canonical_ids_from_database_facade():
    router = build_router(ScopedDatabase(known_ids=["b-doc", "a-doc"]))

    search_tool = next(tool for tool in router._build_response_tools() if tool["name"] == "search_rag_database")

    assert search_tool["parameters"]["properties"]["canonical_source_ids"]["items"]["enum"] == ["a-doc", "b-doc"]
    assert "scope_hints" in search_tool["parameters"]["properties"]
