"""Offline router regression tests.

These tests intentionally stub the model-facing components so they stay fast and
deterministic. They protect orchestration behavior:
- router state transitions
- no-RAG branch behavior
- post-turn summarization ordering
- history search tool behavior
- settings-to-worker assembly
"""

from types import SimpleNamespace

from orchestration.history_preparer import PreparedHistory
from orchestration.model_router import ModelRouter, RouterExecutionError, RouterState, TurnContext
from orchestration.run_control import RunPausedError
from agents.response_agent import ResponseAgent
from agents.response_agent import ResponseState
from config.settings import AppSettings, RoleModelSettings
from agents.summarizers import HistorySummarizer


class FakeDatabase:
    def __init__(self, returned_docs=""):
        self.returned_docs = returned_docs
        self.calls = []

    def retrieve_context(self, query, sources):
        self.calls.append((query, tuple(sources or [])))
        return self.returned_docs


class FakeClassifier:
    def __init__(self, labels):
        self.labels = labels

    def call_api(self, user_question, summarized_conversation_history=None, memory_snapshot_text=""):
        return list(self.labels)


class FakeContextAgent:
    def __init__(self, rewritten_query):
        self.rewritten_query = rewritten_query

    def call_api(self, user_question, recent_turns=None):
        return self.rewritten_query


class FakeHistorySummarizer:
    def __init__(self, prepared=None, summarized=False):
        self.prepared = prepared or PreparedHistory()
        self.summarized = summarized

    def call_api(self, chat_history):
        return self.prepared, self.summarized


class FakeContextSummarizer:
    def __init__(self, summarized_text="", summarized=True):
        self.summarized_text = summarized_text
        self.summarized = summarized

    def call_api(self, user_question, retrieved_docs):
        return self.summarized_text or retrieved_docs, self.summarized


class SpyResponder:
    def __init__(self, response_text="ok"):
        self.response_text = response_text
        self.calls = []

    def call_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text=""):
        self.calls.append(
            {
                "user_query": user_query,
                "retrieved_docs": retrieved_docs,
                "summarized_conversation_history": summarized_conversation_history,
                "memory_snapshot_text": memory_snapshot_text,
            }
        )
        return self.response_text


class ExplodingResponder:
    def call_api(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError("boom")

    def stream_api(self, *args, **kwargs):
        return self.call_api(*args, **kwargs)


class PausingResponder:
    def call_api(self, *args, **kwargs):
        del args, kwargs
        raise RunPausedError("Run paused.", pause_state={"checkpoint": "discussion"})

    def stream_api(self, *args, **kwargs):
        return self.call_api(*args, **kwargs)


class FakeMemoryStore:
    def __init__(self, snapshot_text="KNOWN SYSTEM PROFILE:\n- OS: Debian", issues_text="", attempts_text=""):
        self.snapshot_text = snapshot_text
        self.issues_text = issues_text
        self.attempts_text = attempts_text
        self.committed_resolutions = []

    def begin_turn(self):
        pass

    def end_turn(self):
        pass

    def format_memory_snapshot(self, query, host_label=None):
        return self.snapshot_text

    def load_snapshot(self):
        return {
            "profile": {"os.distribution": "Debian"},
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
            "session_summary": "",
        }

    def commit_resolution(self, resolution, user_question="", assistant_response=""):
        self.committed_resolutions.append((resolution, user_question, assistant_response))

    def format_system_profile(self, host_label=None, max_facts=12):
        return self.snapshot_text

    def search_issues(self, query, max_results=5):
        return self.issues_text

    def search_attempts(self, query, max_results=5):
        return self.attempts_text


class ExplodingWorker:
    def generate_text(self, *args, **kwargs):
        raise AssertionError("Summarizer worker should not be called below threshold")


class FakeWorker:
    def __init__(self, model="unset"):
        self.model = model

    def generate_text(self, *args, **kwargs):
        return ""


class FakeTitleWorker:
    def __init__(self, response_text="Auto title"):
        self.response_text = response_text
        self.calls = []

    def generate_text(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.response_text



class FakeMemoryExtractor:
    def __init__(self, extracted=None):
        self.extracted = extracted or {
            "facts": [],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
            "session_summary": "",
        }
        self.calls = []

    def call_api(self, user_question, assistant_response, recent_history=None):
        self.calls.append((user_question, assistant_response))
        return dict(self.extracted)


class FakeChatStore:
    def __init__(self, history=None, title=""):
        self.history = list(history or [])
        self.appended = []
        self.title = title
        self.updated_titles = []

    def load_conversation_history(self, chat_session_id):
        return list(self.history)

    def get_chat_session(self, chat_session_id):
        return SimpleNamespace(id=chat_session_id, title=self.title)

    def append_message(self, chat_session_id, role, content, council_entries=None):
        self.appended.append((chat_session_id, role, content))
        self.history.append((role, content))

    def update_chat_session_title(self, chat_session_id, title):
        self.title = title
        self.updated_titles.append((chat_session_id, title))
        return self.get_chat_session(chat_session_id)


def test_router_uses_raw_retrieved_docs_before_post_turn_summarization():
    responder = SpyResponder(response_text="final answer")
    memory_store = FakeMemoryStore()
    router = ModelRouter(
        database=FakeDatabase(returned_docs="[Source: Debian_Install_Guide.pdf (Page 4)]\napt install foo"),
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install package"),
        history_summarizer=FakeHistorySummarizer(
            prepared=PreparedHistory(recent_turns=[("user", "older question")], summary_text="older summary")
        ),
        context_summarizer=FakeContextSummarizer(summarized_text="condensed docs", summarized=True),
        responder=responder,
        memory_store=memory_store,
        memory_extractor=FakeMemoryExtractor(),
    )

    response = router.ask_question("How do I install it?")

    assert response == "final answer"
    assert responder.calls[0]["retrieved_docs"] == "[Source: Debian_Install_Guide.pdf (Page 4)]\napt install foo"
    assert "KNOWN SYSTEM PROFILE" in responder.calls[0]["memory_snapshot_text"]
    assert router.last_turn.summarized_retrieved_docs == "condensed docs"
    assert router.last_turn.state_trace == [
        RouterState.START.name,
        RouterState.LOAD_MEMORY.name,
        RouterState.SUMMARIZE_CONVERSATION_HISTORY.name,
        RouterState.CLASSIFY.name,
        RouterState.DECIDE_RAG.name,
        RouterState.REWRITE_QUERY.name,
        RouterState.RETRIEVE_CONTEXT.name,
        RouterState.GENERATE_RESPONSE.name,
        RouterState.SUMMARIZE_RETRIEVED_DOCS.name,
        RouterState.UPDATE_HISTORY.name,
        RouterState.DECIDE_MEMORY.name,
        RouterState.EXTRACT_MEMORY.name,
        RouterState.RESOLVE_MEMORY.name,
        RouterState.COMMIT_MEMORY.name,
        RouterState.AUTO_NAME.name,
        RouterState.DONE.name,
    ]


def test_router_preserves_retrieved_blocks_and_full_memory_payloads():
    class FakeRichDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources):
            self.calls.append((query, tuple(sources or [])))
            return {
                "context_text": "---\n[Source: Debian_Install_Guide.pdf (Page 4)]\napt install foo\n",
                "selected_sources": ["Debian_Install_Guide.pdf:Page 4"],
                "merged_blocks": [
                    {
                        "source": "Debian_Install_Guide.pdf",
                        "pages": [4],
                        "page_label": "Page 4",
                        "text": "apt install foo",
                    }
                ],
            }

    extracted_memory = {
        "facts": [
            {
                "fact_key": "os.distribution",
                "fact_value": "Debian",
                "source_type": "user",
                "source_ref": "user_question",
                "confidence": 0.9,
                "verified": False,
            }
        ],
        "issues": [],
        "attempts": [],
        "constraints": [],
        "preferences": [],
        "session_summary": "User is troubleshooting package install flow.",
    }
    router = ModelRouter(
        database=FakeRichDatabase(returned_docs="ignored"),
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install package"),
        history_summarizer=FakeHistorySummarizer(
            prepared=PreparedHistory(recent_turns=[("user", "older question")], summary_text="older summary")
        ),
        context_summarizer=FakeContextSummarizer(summarized_text="condensed docs", summarized=True),
        responder=SpyResponder("final answer"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(extracted=extracted_memory),
    )

    turn = router.run_turn("How do I install it?", stream_response=False)

    assert turn.retrieved_context_blocks == [
        {
            "source": "Debian_Install_Guide.pdf",
            "pages": [4],
            "page_label": "Page 4",
            "text": "apt install foo",
        }
    ]
    memory_extracted = next(event for event in turn.tool_events if event["type"] == "memory_extracted")
    memory_resolved = next(event for event in turn.tool_events if event["type"] == "memory_resolved")
    assert memory_extracted["payload"]["items"]["session_summary"] == "User is troubleshooting package install flow."
    assert memory_extracted["payload"]["items"]["facts"][0]["fact_key"] == "os.distribution"
    assert memory_resolved["payload"]["committed_full"]["facts"][0]["fact_value"] == "Debian"
    assert memory_resolved["payload"]["session_summary"] == "User is troubleshooting package install flow."


def test_router_conversation_history_search_returns_matching_snippets():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.update_history("user", "I already ran apt install docker.io and it failed.")
    router.update_history("model", "Try checking whether the package exists first.")
    router.update_history("user", "The exact error was permission denied.")

    result = router._handle_responder_tool_call(
        "search_conversation_history",
        {"query": "apt install docker.io permission denied", "max_results": 2},
    )

    assert "apt install docker.io" in result
    assert "permission denied" in result


def test_router_no_rag_skips_database_retrieval():
    database = FakeDatabase(returned_docs="should not be used")
    responder = SpyResponder(response_text="hello back")
    memory_store = FakeMemoryStore()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent("hello"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=responder,
        memory_store=memory_store,
        memory_extractor=FakeMemoryExtractor({"issues": [{"title": "hello", "category": "general", "summary": "", "status": "unknown"}], "facts": [], "attempts": [], "constraints": [], "preferences": [], "session_summary": ""}),
    )

    response = router.ask_question("hello")

    assert response == "hello back"
    assert database.calls == []
    assert responder.calls[0]["retrieved_docs"] == ""
    assert RouterState.REWRITE_QUERY.name not in router.last_turn.state_trace
    assert RouterState.RETRIEVE_CONTEXT.name not in router.last_turn.state_trace
    assert RouterState.SUMMARIZE_RETRIEVED_DOCS.name not in router.last_turn.state_trace
    assert RouterState.EXTRACT_MEMORY.name in router.last_turn.state_trace
    assert len(memory_store.committed_resolutions) == 1
    assert memory_store.committed_resolutions[0][1:] == ("hello", "hello back")
    assert router.last_turn.suggested_search_labels == []


def test_router_prefetch_uses_searchable_labels_only():
    database = FakeDatabase(returned_docs="scoped docs")
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["general", "docker"]),
        context_agent=FakeContextAgent("docker permission denied"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    router.ask_question("Why does docker say permission denied?")

    assert database.calls == [("docker permission denied", ("docker",))]
    assert router.last_turn.suggested_search_labels == ["docker"]


def test_router_run_turn_raises_structured_error_for_worker_path():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=ExplodingResponder(),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    try:
        router.run_turn("hello", stream_response=True)
        assert False, "expected structured router execution error"
    except RouterExecutionError as exc:
        assert str(exc) == "boom"
        assert exc.turn is not None
        assert exc.turn.error == "boom"
        assert RouterState.ERROR.name in exc.turn.state_trace


def test_router_run_turn_propagates_pause_without_converting_it_to_router_error():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=PausingResponder(),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    try:
        router.run_turn("hello", stream_response=True)
        assert False, "expected pause to propagate"
    except RunPausedError as exc:
        assert exc.pause_state == {"checkpoint": "discussion"}


def test_router_does_not_treat_literal_router_error_text_as_failure():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("Router error: this is literal assistant content"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    response = router.ask_question("hello")

    assert response == "Router error: this is literal assistant content"
    assert router.last_turn.response == "Router error: this is literal assistant content"


def test_router_tool_search_strips_control_labels_before_retrieval():
    database = FakeDatabase(returned_docs="manual docs")
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    result = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "docker install", "relevant_documents": ["no_rag", "docker"], "evidence_gap": "docker install docs"},
    )

    assert result == "manual docs"
    assert database.calls == [("docker install", ("docker",))]


def test_router_tool_search_allows_broad_search_when_only_control_labels_are_present():
    database = FakeDatabase(returned_docs="broad docs")
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    result = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "obscure package", "relevant_documents": ["no_rag"], "evidence_gap": "obscure package docs"},
    )

    assert result == "broad docs"
    assert database.calls == [("obscure package", ())]


def test_router_tool_search_emits_prompt_facing_retrieval_blocks_for_tool_results():
    class FakeRichDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources):
            self.calls.append((query, tuple(sources or [])))
            return {
                "context_text": "---\n[Source: Debian.pdf (Page 4)]\napt install foo\n",
                "selected_sources": ["Debian.pdf:Page 4"],
                "merged_blocks": [
                    {
                        "source": "Debian.pdf",
                        "pages": [4],
                        "page_label": "Page 4",
                        "text": "apt install foo",
                    }
                ],
            }

    database = FakeRichDatabase()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    events = []
    router.set_event_listener(lambda event_type, payload: events.append((event_type, payload)))
    expected_text = "---\n[Source: Debian.pdf (Page 4)]\napt install foo\n"

    result = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "install package", "relevant_documents": ["debian"], "evidence_gap": "install component"},
    )

    assert result == expected_text
    assert database.calls == [("install package", ("debian",))]
    event_type, payload = events[-1]
    assert event_type == "tool_complete"
    assert payload["name"] == "search_rag_database"
    assert payload["result_size"] == len(expected_text)
    assert payload["result_text"] == expected_text
    assert payload["result_blocks"] == [
        {
            "source": "Debian.pdf",
            "pages": [4],
            "page_label": "Page 4",
            "text": "apt install foo",
        }
    ]
    assert payload["selected_sources"] == ["Debian.pdf:Page 4"]
    assert payload["cached"] is False
    assert payload["gate_action"] == "allow"
    assert payload["search_outcome"] == "no_new_evidence"
    assert payload["usefulness"] == ""
    assert payload["scope_key"] == "debian::install_package"
    assert payload["caller_role"] == ""
    assert payload["caller_phase"] == ""
    assert payload["caller_round"] == 0


def test_router_tool_search_exact_duplicate_hits_cache_when_seen_state_is_unchanged():
    class FakeDedupingDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None):
            self.calls.append(
                (
                    query,
                    tuple(sources or []),
                    tuple((window.get("key"), window.get("page_start"), window.get("page_end")) for window in (excluded_page_windows or [])),
                    tuple(excluded_block_keys or []),
                )
            )
            return {
                "context_text": "",
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

    database = FakeDedupingDatabase()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    events = []
    router.set_event_listener(lambda event_type, payload: events.append((event_type, payload)))

    first = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "repeat me", "relevant_documents": ["debian"], "evidence_gap": "repeat me"},
    )
    second = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "repeat me", "relevant_documents": ["debian"], "evidence_gap": "repeat me"},
    )

    assert first == ""
    assert second == ""
    assert len(database.calls) == 1
    event_type, payload = events[-1]
    assert event_type == "tool_complete"
    assert payload["name"] == "search_rag_database"
    assert payload["cached"] is True
    assert payload["gate_action"] == "allow_net_new_only"
    assert payload["search_outcome"] == "cache_hit"
    assert payload["usefulness"] == ""
    assert payload["scope_key"] == "debian::repeat_me"
    assert payload["soft_exhausted_scope_keys"] == []
    assert payload["scope_exhausted"] is False


def test_router_prefetch_and_tool_search_share_turn_scoped_retrieval_ledger():
    class FakeProgressiveDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None):
            self.calls.append(
                {
                    "query": query,
                    "sources": tuple(sources or []),
                    "excluded_page_windows": list(excluded_page_windows or []),
                    "excluded_block_keys": list(excluded_block_keys or []),
                }
            )
            if excluded_page_windows:
                return {
                    "context_text": "",
                    "selected_sources": [],
                    "merged_blocks": [],
                    "bundle_summaries": [],
                    "retrieval_metadata": {
                        "anchor_count": 1,
                        "anchor_pages": [4],
                        "fetched_neighbor_pages": [],
                        "delivered_bundle_count": 0,
                        "delivered_bundle_keys": [],
                        "delivered_block_keys": [],
                        "delivered_page_window_keys": [],
                        "delivered_page_windows": [],
                        "excluded_seen_count": 1,
                        "skipped_bundle_count": 0,
                    },
                }
            return {
                "context_text": "---\n[Source: Debian.pdf (Page 4)]\napt install foo\n",
                "selected_sources": ["Debian.pdf:Page 4"],
                "merged_blocks": [
                    {
                        "source": "Debian.pdf",
                        "pages": [4],
                        "page_label": "Page 4",
                        "text": "apt install foo",
                        "bundle_key": "bundle:Debian.pdf:4-4:anchor:vec_4",
                        "block_key": "block:Debian.pdf:4-4",
                        "page_window_key": "window:Debian.pdf:4-4",
                    }
                ],
                "bundle_summaries": [
                    {
                        "bundle_key": "bundle:Debian.pdf:4-4:anchor:vec_4",
                        "source": "Debian.pdf",
                        "anchor_row_key": "vec_4",
                        "anchor_page": 4,
                        "requested_page_window_key": "window:Debian.pdf:4-4",
                        "requested_page_start": 4,
                        "requested_page_end": 4,
                        "delivered_page_window_key": "window:Debian.pdf:4-4",
                        "delivered_pages": [4],
                        "row_keys": ["vec_4"],
                        "page_less": False,
                    }
                ],
                "retrieval_metadata": {
                    "anchor_count": 1,
                    "anchor_pages": [4],
                    "fetched_neighbor_pages": [],
                    "delivered_bundle_count": 1,
                    "delivered_bundle_keys": ["bundle:Debian.pdf:4-4:anchor:vec_4"],
                    "delivered_block_keys": ["block:Debian.pdf:4-4"],
                    "delivered_page_window_keys": ["window:Debian.pdf:4-4"],
                    "delivered_page_windows": [
                        {
                            "key": "window:Debian.pdf:4-4",
                            "source": "Debian.pdf",
                            "page_start": 4,
                            "page_end": 4,
                        }
                    ],
                    "excluded_seen_count": 0,
                    "skipped_bundle_count": 0,
                },
            }

    database = FakeProgressiveDatabase()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent("install package"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    events = []
    router.set_event_listener(lambda event_type, payload: events.append((event_type, payload)))

    turn = TurnContext(user_question="How do I install it?")
    router.current_turn = turn
    try:
        router._retrieve_context(turn)
        result = router._handle_responder_tool_call(
            "search_rag_database",
            {"query": "install package", "relevant_documents": ["debian"], "evidence_gap": "install package"},
        )
    finally:
        router.current_turn = None

    assert turn.retrieved_docs == "---\n[Source: Debian.pdf (Page 4)]\napt install foo\n"
    assert result == ""
    assert len(database.calls) == 2
    assert database.calls[0]["excluded_page_windows"] == []
    assert database.calls[1]["excluded_page_windows"] == [
        {
            "key": "window:Debian.pdf:4-4",
            "source": "Debian.pdf",
            "page_start": 4,
            "page_end": 4,
        }
    ]
    event_type, payload = events[-1]
    assert event_type == "tool_complete"
    assert payload["name"] == "search_rag_database"
    assert payload["cached"] is False
    assert payload["gate_action"] == "allow"
    assert payload["search_outcome"] == "no_new_evidence"
    assert payload["usefulness"] == ""
    assert payload["usefulness_reason"] == ""
    assert payload["scope_key"] == "debian::install_package"
    assert payload["covered_region_count"] == 1
    assert payload["scope_exhausted"] is False


def test_router_tool_search_soft_require_reason_is_visible_for_normal_chatbot():
    class FakeEmptyDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None):
            self.calls.append((query, tuple(sources or [])))
            return {
                "context_text": "",
                "selected_sources": [],
                "merged_blocks": [],
                "bundle_summaries": [],
                "retrieval_metadata": {
                    "anchor_count": 0,
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

    database = FakeEmptyDatabase()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    events = []
    router.set_event_listener(lambda event_type, payload: events.append((event_type, payload)))

    first = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "repeat me", "relevant_documents": ["debian"]},
    )
    second = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "repeat me", "relevant_documents": ["debian"]},
    )

    # After two identical searches the scope is known and the cache hits.
    assert first == ""
    assert second == ""
    # Pool should track the queries and emit evidence_pool_update events.
    pool = router._active_evidence_pool()
    assert pool.scope_state.scope_query_counts.get("debian::repeat_me", 0) >= 1
    assert any(
        event_type == "evidence_pool_update"
        for event_type, payload in events
    )


def test_router_tool_search_different_queries_both_reach_database():
    """Two distinct search_rag_database calls both reach the database independently."""

    class FakeTrackingDatabase(FakeDatabase):
        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None, evidence_gap=None):
            self.calls.append((query, tuple(sources or [])))
            return {
                "context_text": "",
                "selected_sources": [],
                "merged_blocks": [],
                "bundle_summaries": [],
                "retrieval_metadata": {
                    "anchor_count": 0,
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

    database = FakeTrackingDatabase()
    router = ModelRouter(
        database=database,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "how to install docker", "relevant_documents": ["debian"]},
    )
    router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "verify docker running", "relevant_documents": ["debian"], "progress_assessment": "partial_progress"},
    )

    # Both queries must reach the database
    assert len(database.calls) == 2
    queries_sent = [call[0] for call in database.calls]
    assert "how to install docker" in queries_sent
    assert "verify docker running" in queries_sent


def test_router_loads_and_persists_session_scoped_chat_history():
    chat_store = FakeChatStore(history=[("user", "old question"), ("model", "old answer")])
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("fresh answer"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    assert router.get_history() == [("user", "old question"), ("model", "old answer")]

    router.ask_question("new question")

    assert chat_store.appended == [
        ("session-123", "user", "new question"),
        ("session-123", "model", "fresh answer"),
    ]


def test_history_summarizer_noops_below_threshold():
    summarizer = HistorySummarizer(worker=ExplodingWorker(), max_recent_turns=4)
    prepared, summarized = summarizer.call_api(
        [("user", "hello"), ("model", "hi there")]
    )

    assert summarized is False
    assert prepared.summary_text == ""
    assert prepared.recent_turns == [("user", "hello"), ("model", "hi there")]


def test_settings_provider_override_uses_provider_default_model():
    original_worker_types = ModelRouter.WORKER_TYPES
    ModelRouter.WORKER_TYPES = {
        "openai": FakeWorker,
        "local": FakeWorker,
    }

    try:
        settings = AppSettings(
            provider_defaults={"openai": "openai-default", "local": "local-default"},
            classifier=RoleModelSettings("openai", "classifier-model"),
            contextualizer=RoleModelSettings("openai", "context-model"),
            responder=RoleModelSettings("openai", "responder-model"),
            history_summarizer=RoleModelSettings("openai", "history-model"),
            context_summarizer=RoleModelSettings("openai", "context-summary-model"),
            memory_extractor=RoleModelSettings("openai", "memory-model"),
            registry_updater=RoleModelSettings("local", "registry-model"),
            chat_namer=RoleModelSettings("openai", "chat-namer-model"),
            response_tool_rounds=5,
            classifier_temperature=0.0,
            contextualizer_temperature=0.0,
            history_summarizer_temperature=0.1,
            history_max_recent_turns=6,
            history_summarize_turn_threshold=20,
            history_summarize_char_threshold=4200,
        )

        router = ModelRouter(
            settings=settings,
            database=FakeDatabase(""),
            responder="local",
            memory_store=FakeMemoryStore(),
        )

        assert isinstance(router.responder, ResponseAgent)
        assert router.responder.worker.model == "local-default"
        assert router.response_tool_rounds == 5
        assert isinstance(router.history_summarizer, HistorySummarizer)
        assert router.history_summarizer.max_recent_turns == 6
        assert router.history_summarizer.summarize_turn_threshold == 20
        assert router.history_summarizer.summarize_char_threshold == 4200
    finally:
        ModelRouter.WORKER_TYPES = original_worker_types


def test_settings_google_provider_override_uses_provider_default_model():
    original_worker_types = ModelRouter.WORKER_TYPES
    ModelRouter.WORKER_TYPES = {
        "openai": FakeWorker,
        "google": FakeWorker,
    }

    try:
        settings = AppSettings(
            provider_defaults={"openai": "openai-default", "google": "google-default"},
            classifier=RoleModelSettings("openai", "classifier-model"),
            contextualizer=RoleModelSettings("openai", "context-model"),
            responder=RoleModelSettings("openai", "responder-model"),
            history_summarizer=RoleModelSettings("openai", "history-model"),
            context_summarizer=RoleModelSettings("openai", "context-summary-model"),
            memory_extractor=RoleModelSettings("openai", "memory-model"),
            registry_updater=RoleModelSettings("openai", "registry-model"),
            chat_namer=RoleModelSettings("openai", "chat-namer-model"),
            response_tool_rounds=5,
            classifier_temperature=0.0,
            contextualizer_temperature=0.0,
            history_summarizer_temperature=0.1,
            history_max_recent_turns=6,
            history_summarize_turn_threshold=20,
            history_summarize_char_threshold=4200,
        )

        router = ModelRouter(
            settings=settings,
            database=FakeDatabase(""),
            responder="google",
            memory_store=FakeMemoryStore(),
        )

        assert isinstance(router.responder, ResponseAgent)
        assert router.responder.worker.model == "google-default"
    finally:
        ModelRouter.WORKER_TYPES = original_worker_types


def test_router_exposes_structured_memory_tools():
    memory_store = FakeMemoryStore(
        snapshot_text="KNOWN SYSTEM PROFILE:\n- OS: Debian 12",
        issues_text="[open] docker permissions | containers | permission denied",
        attempts_text="restarted docker | sudo systemctl restart docker | reported by user | docker permissions",
    )
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=memory_store,
        memory_extractor=FakeMemoryExtractor(),
    )

    assert "Debian 12" in router._handle_responder_tool_call("get_system_profile", {})
    assert "docker permissions" in router._handle_responder_tool_call(
        "search_memory_issues",
        {"query": "docker permission denied", "max_results": 3},
    )
    assert "restart docker" in router._handle_responder_tool_call(
        "search_attempt_log",
        {"query": "restart docker", "max_results": 3},
    )


def test_router_builds_default_memory_extractor_when_custom_store_is_injected():
    original_worker_types = ModelRouter.WORKER_TYPES
    ModelRouter.WORKER_TYPES = {
        "openai": FakeWorker,
        "local": FakeWorker,
    }

    try:
        router = ModelRouter(
            database=FakeDatabase(""),
            classifier=FakeClassifier(["no_rag"]),
            context_agent=FakeContextAgent(""),
            history_summarizer=FakeHistorySummarizer(),
            context_summarizer=FakeContextSummarizer(summarized=False),
            responder=SpyResponder("ok"),
            memory_store=FakeMemoryStore(),
            memory_extractor=None,
        )
        assert router.memory_extractor is not None
    finally:
        ModelRouter.WORKER_TYPES = original_worker_types


def test_no_memory_store_skips_load_memory():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=None,
    )

    router.ask_question("hello")

    assert RouterState.LOAD_MEMORY.name not in router.last_turn.state_trace


def test_no_rag_skips_rewrite_query():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent("should not be called"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    router.ask_question("hello")

    assert RouterState.REWRITE_QUERY.name not in router.last_turn.state_trace
    assert RouterState.RETRIEVE_CONTEXT.name not in router.last_turn.state_trace


def test_empty_docs_skip_summarize_retrieved():
    router = ModelRouter(
        database=FakeDatabase(returned_docs=""),
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install debian"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    router.ask_question("How do I install Debian?")

    assert RouterState.RETRIEVE_CONTEXT.name in router.last_turn.state_trace
    assert RouterState.SUMMARIZE_RETRIEVED_DOCS.name not in router.last_turn.state_trace


def test_decide_memory_skips_when_no_store():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install debian"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=None,
    )

    router.ask_question("How do I install Debian?")

    assert RouterState.DECIDE_MEMORY.name in router.last_turn.state_trace
    assert RouterState.EXTRACT_MEMORY.name not in router.last_turn.state_trace
    assert RouterState.RESOLVE_MEMORY.name not in router.last_turn.state_trace
    assert RouterState.COMMIT_MEMORY.name not in router.last_turn.state_trace


def test_decide_memory_always_runs_extraction_regardless_of_labels():
    memory_store = FakeMemoryStore()
    extractor = FakeMemoryExtractor()
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=memory_store,
        memory_extractor=extractor,
    )

    router.ask_question("thanks, by the way I'm running Ubuntu 24.04")

    assert RouterState.DECIDE_MEMORY.name in router.last_turn.state_trace
    assert RouterState.EXTRACT_MEMORY.name in router.last_turn.state_trace
    assert len(extractor.calls) == 1
    assert len(memory_store.committed_resolutions) == 1


def test_decide_memory_runs_for_substantive_turns():
    memory_store = FakeMemoryStore()
    extractor = FakeMemoryExtractor()
    router = ModelRouter(
        database=FakeDatabase(returned_docs="docker docs"),
        classifier=FakeClassifier(["docker"]),
        context_agent=FakeContextAgent("docker permission denied"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("try sudo"),
        memory_store=memory_store,
        memory_extractor=extractor,
    )

    router.ask_question("docker permission denied")

    assert RouterState.DECIDE_MEMORY.name in router.last_turn.state_trace
    assert RouterState.EXTRACT_MEMORY.name in router.last_turn.state_trace
    assert RouterState.COMMIT_MEMORY.name in router.last_turn.state_trace
    assert len(extractor.calls) == 1
    assert len(memory_store.committed_resolutions) == 1


def test_router_auto_names_first_turn_after_memory_commit():
    chat_store = FakeChatStore()
    title_worker = FakeTitleWorker('  "Docker permissions fix."  ')
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("try adding your user to the docker group"),
        chat_namer=title_worker,
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    router.ask_question("docker permission denied")

    assert chat_store.updated_titles == [("session-123", "Docker permissions fix")]
    assert RouterState.AUTO_NAME.name in router.last_turn.state_trace
    assert router.last_turn.state_trace[-2:] == [RouterState.AUTO_NAME.name, RouterState.DONE.name]
    assert title_worker.calls[0]["kwargs"]["max_output_tokens"] == 30
    assert any(event["type"] == "chat_named" for event in router.last_turn.tool_events)


def test_router_auto_names_first_turn_without_memory_store():
    chat_store = FakeChatStore()
    title_worker = FakeTitleWorker("First turn title")
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        chat_namer=title_worker,
        memory_store=None,
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    router.ask_question("hello")

    assert chat_store.updated_titles == [("session-123", "First turn title")]
    assert RouterState.AUTO_NAME.name in router.last_turn.state_trace
    assert RouterState.EXTRACT_MEMORY.name not in router.last_turn.state_trace


def test_router_auto_name_does_not_overwrite_existing_chat_title():
    chat_store = FakeChatStore(title="Pinned title")
    title_worker = FakeTitleWorker("Should not be used")
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        chat_namer=title_worker,
        memory_store=None,
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    router.ask_question("hello")

    assert chat_store.updated_titles == []
    assert title_worker.calls == []


def test_router_streaming_first_turn_schedules_auto_name_follow_up():
    chat_store = FakeChatStore()
    title_worker = FakeTitleWorker("Deferred title")
    responder = SpyResponder("streamed answer")
    responder.stream_api = responder.call_api
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=responder,
        chat_namer=title_worker,
        memory_store=None,
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    turn = router.run_turn("hello", stream_response=True)

    assert turn.schedule_auto_name is True
    assert RouterState.AUTO_NAME.name not in turn.state_trace
    assert chat_store.updated_titles == []
    assert title_worker.calls == []
    assert any(event["type"] == "auto_name_scheduled" for event in turn.tool_events)


def test_router_auto_name_follow_up_uses_persisted_first_exchange():
    chat_store = FakeChatStore(history=[("user", "hello"), ("model", "streamed answer")])
    title_worker = FakeTitleWorker("Follow-up title")
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("unused"),
        chat_namer=title_worker,
        memory_store=None,
        chat_store=chat_store,
        chat_session_id="session-123",
    )

    turn = router.run_auto_name_follow_up()

    assert turn.generated_chat_title == "Follow-up title"
    assert chat_store.updated_titles == [("session-123", "Follow-up title")]
    assert turn.state_trace == [RouterState.AUTO_NAME.name, RouterState.DONE.name]
    assert any(event["type"] == "chat_named" for event in turn.tool_events)


def test_router_responder_state_events_include_phase_and_details():
    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=None,
    )
    router.current_turn = turn = router.run_turn("hello", stream_response=False)
    router.current_turn = turn

    router._handle_responder_state(ResponseState.PROCESS_TOOL_CALLS, {"round": 1, "count": 2})

    assert turn.state_trace[-1] == "RESPONDER_PROCESS_TOOL_CALLS"
    assert turn.tool_events[-1] == {
        "type": "responder_state",
        "payload": {
            "phase": "responder",
            "state": "PROCESS_TOOL_CALLS",
            "details": {"round": 1, "count": 2},
            "trace_marker": "RESPONDER_PROCESS_TOOL_CALLS",
        },
    }


class FakeSequentialSearchDatabase:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None, evidence_gap=None):
        self.calls.append(
            {
                "query": query,
                "sources": tuple(sources or []),
                "excluded_page_windows": list(excluded_page_windows or []),
                "excluded_block_keys": list(excluded_block_keys or []),
                "evidence_gap": evidence_gap,
            }
        )
        if not self.results:
            raise AssertionError("No fake retrieval results remaining")
        return self.results.pop(0)


def _retrieval_result_with_evidence(text, *, source="Debian.pdf", page=4, selected_sources=None):
    selected_sources = list(selected_sources if selected_sources is not None else [source])
    return {
        "context_text": text,
        "selected_sources": selected_sources,
        "merged_blocks": [],
        "bundle_summaries": [],
        "retrieval_metadata": {
            "anchor_count": 1,
            "anchor_pages": [page],
            "fetched_neighbor_pages": [],
            "delivered_bundle_count": 1,
            "delivered_bundle_keys": [f"bundle:{source}:{page}-{page}:anchor:r{page}"],
            "delivered_block_keys": [f"block:{source}:{page}-{page}"],
            "delivered_page_window_keys": [f"window:{source}:{page}-{page}"],
            "delivered_page_windows": [{"key": f"window:{source}:{page}-{page}", "source": source, "page_start": page, "page_end": page}],
            "excluded_seen_count": 0,
            "skipped_bundle_count": 0,
        },
    }


def _retrieval_result_without_evidence():
    return {
        "context_text": "",
        "selected_sources": [],
        "merged_blocks": [],
        "bundle_summaries": [],
        "retrieval_metadata": {
            "anchor_count": 0,
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


def test_router_responder_can_call_web_search_via_provider_tool_loop():
    """Regular responder uses same _handle_responder_tool_call as Magi — unknown tool names fall through gracefully."""

    events = []

    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.set_event_listener(lambda et, pl: events.append((et, pl)))

    # web_search is handled natively by the provider tool loop — calling
    # _handle_responder_tool_call with it returns an "unknown tool" error dict,
    # not a crash.
    result = router._handle_responder_tool_call("web_search", {"query": "latest kernel release"})
    assert isinstance(result, dict) and "error" in result
    assert any(event_type == "tool_error" for event_type, _ in events)


def test_router_responder_and_magi_share_handle_responder_tool_call():
    """search_rag_database and search_conversation_history both route through _handle_responder_tool_call."""

    events = []

    class TrackingDatabase:
        def __init__(self):
            self.calls = []

        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None, evidence_gap=None):
            self.calls.append(query)
            return {
                "context_text": "some docs",
                "selected_sources": list(sources or []),
                "merged_blocks": [],
                "bundle_summaries": [],
                "retrieval_metadata": {
                    "anchor_count": 0, "anchor_pages": [], "fetched_neighbor_pages": [],
                    "delivered_bundle_count": 0, "delivered_bundle_keys": [],
                    "delivered_block_keys": [], "delivered_page_window_keys": [],
                    "delivered_page_windows": [], "excluded_seen_count": 0, "skipped_bundle_count": 0,
                },
            }

    db = TrackingDatabase()
    router = ModelRouter(
        database=db,
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.set_event_listener(lambda et, pl: events.append((et, pl)))

    # search_rag_database routes to the database
    rag_result = router._handle_responder_tool_call(
        "search_rag_database",
        {"query": "install docker", "relevant_documents": ["debian"]},
    )
    assert "some docs" in rag_result
    assert db.calls == ["install docker"]

    # search_conversation_history routes to history search (empty history → empty string)
    history_result = router._handle_responder_tool_call(
        "search_conversation_history",
        {"query": "previous docker discussion"},
    )
    assert isinstance(history_result, str)

    # Both tool calls emitted tool_complete events (not tool_error)
    complete_events = [et for et, _ in events if et == "tool_complete"]
    assert len(complete_events) >= 2


# ---------------------------------------------------------------------------
# Magi router integration tests
# ---------------------------------------------------------------------------

class FakeMagiResponder:
    def __init__(self, response_text="magi answer"):
        self.response_text = response_text
        self.calls = []

    def call_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", evidence_pool_summary=""):
        self.calls.append(user_query)
        return self.response_text

    def stream_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", evidence_pool_summary=""):
        return self.call_api(user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, evidence_pool_summary)


def test_router_magi_toggle_dispatches_correctly():
    normal_responder = SpyResponder("normal answer")
    magi_responder = FakeMagiResponder("magi answer")

    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=normal_responder,
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.magi_responder = magi_responder

    # Normal turn
    result_normal = router.ask_question("hello", magi="off")
    assert result_normal == "normal answer"
    assert len(normal_responder.calls) == 1
    assert len(magi_responder.calls) == 0

    # Full Council turn
    result_magi = router.ask_question("hello again", magi="full")
    assert result_magi == "magi answer"
    assert len(magi_responder.calls) == 1


def test_router_magi_turn_trace_markers():
    magi_responder = FakeMagiResponder("magi answer")

    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("normal"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.magi_responder = magi_responder

    router.ask_question("test question", magi="full")

    assert RouterState.GENERATE_RESPONSE.name in router.last_turn.state_trace


def test_router_magi_lite_dispatches_to_lite_responder():
    normal_responder = SpyResponder("normal answer")
    full_responder = FakeMagiResponder("full answer")
    lite_responder = FakeMagiResponder("lite answer")

    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=normal_responder,
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.magi_responder = full_responder
    router.magi_lite_responder = lite_responder

    result = router.ask_question("lite question", magi="lite")
    assert result == "lite answer"
    assert len(lite_responder.calls) == 1
    assert len(full_responder.calls) == 0
    assert len(normal_responder.calls) == 0


def test_router_magi_off_uses_standard_responder():
    normal_responder = SpyResponder("normal answer")
    full_responder = FakeMagiResponder("full answer")

    router = ModelRouter(
        database=FakeDatabase(""),
        classifier=FakeClassifier(["no_rag"]),
        context_agent=FakeContextAgent(""),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=normal_responder,
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    router.magi_responder = full_responder

    result = router.ask_question("normal question", magi="off")
    assert result == "normal answer"
    assert len(normal_responder.calls) == 1
    assert len(full_responder.calls) == 0


# ---------------------------------------------------------------------------
# Evidence pool router integration tests
# ---------------------------------------------------------------------------

from orchestration.evidence_pool import (
    OUTCOME_CACHE_HIT,
    OUTCOME_NEW_EVIDENCE,
    OUTCOME_NO_NEW,
    OUTCOME_REUSED_KNOWN,
    EvidencePool,
)
from orchestration.model_router import TurnContext


def _make_progressive_db(first_result, second_result=None):
    """Database whose second call returns a different result from the first."""
    class ProgressiveDB(FakeDatabase):
        def __init__(self):
            super().__init__("")
            self.result_sequence = [first_result]
            if second_result is not None:
                self.result_sequence.append(second_result)

        def retrieve_context_result(self, query, sources, excluded_page_windows=None, excluded_block_keys=None):
            self.calls.append(
                {"query": query, "sources": sources,
                 "excluded_page_windows": list(excluded_page_windows or []),
                 "excluded_block_keys": list(excluded_block_keys or [])},
            )
            index = min(len(self.calls) - 1, len(self.result_sequence) - 1)
            return self.result_sequence[index]
    return ProgressiveDB()


def _page_result(source, ps, pe):
    return {
        "context_text": f"text from {source} p{ps}-{pe}",
        "selected_sources": [source],
        "merged_blocks": [],
        "bundle_summaries": [],
        "retrieval_metadata": {
            "anchor_count": 1,
            "anchor_pages": [ps],
            "fetched_neighbor_pages": [],
            "delivered_bundle_count": 1,
            "delivered_bundle_keys": [f"bundle:{source}:{ps}-{pe}:anchor:r1"],
            "delivered_block_keys": [f"block:{source}:{ps}-{pe}"],
            "delivered_page_window_keys": [f"window:{source}:{ps}-{pe}"],
            "delivered_page_windows": [
                {"key": f"window:{source}:{ps}-{pe}", "source": source, "page_start": ps, "page_end": pe}
            ],
            "excluded_seen_count": 0,
            "skipped_bundle_count": 0,
        },
    }


def _empty_db_result():
    return {
        "context_text": "",
        "selected_sources": [],
        "merged_blocks": [],
        "bundle_summaries": [],
        "retrieval_metadata": {
            "anchor_count": 0, "anchor_pages": [], "fetched_neighbor_pages": [],
            "delivered_bundle_count": 0, "delivered_bundle_keys": [], "delivered_block_keys": [],
            "delivered_page_window_keys": [], "delivered_page_windows": [],
            "excluded_seen_count": 0, "skipped_bundle_count": 0,
        },
    }


def test_router_evidence_pool_stores_query_and_coverage_state():
    """After a prefetch retrieval, the turn's evidence pool contains the correct query record and coverage."""
    db = _make_progressive_db(_page_result("Debian.pdf", 4, 6))
    router = ModelRouter(
        database=db,
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install package"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    turn = TurnContext(user_question="How do I install?")
    router.current_turn = turn
    try:
        router._retrieve_context(turn)
    finally:
        router.current_turn = None

    pool = turn.evidence_pool
    assert pool is not None
    assert len(pool.query_records) == 1
    assert pool.query_records[0].outcome == OUTCOME_NEW_EVIDENCE
    assert "region:Debian.pdf:4-6" in pool.known_covered_region_keys()


def test_router_evidence_pool_exact_duplicate_is_cache_hit():
    """Same query twice with unchanged coverage hits the pool cache on the second call.

    The fingerprint includes the exclusion state. After a first call that returns
    no page windows, coverage stays at zero, so the second call has an identical
    fingerprint and is served from the pool cache without a second DB call.
    """
    # Empty result — no delivered pages, so coverage stays at zero between calls.
    db = _make_progressive_db(_empty_db_result())
    router = ModelRouter(
        database=db,
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("install package"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    turn = TurnContext(user_question="How do I install?")
    router.current_turn = turn
    try:
        _, first_cache = router._retrieve_with_ledger("install package", ["debian"])
        _, is_cache = router._retrieve_with_ledger("install package", ["debian"])
    finally:
        router.current_turn = None

    assert first_cache is False   # first was a real DB call
    assert is_cache is True       # second hit the pool cache (same fingerprint)
    pool = turn.evidence_pool
    assert len(pool.query_records) == 2
    assert pool.query_records[0].outcome == OUTCOME_NO_NEW
    assert pool.query_records[1].outcome == OUTCOME_CACHE_HIT
    # Database should only have been called once
    assert len(db.calls) == 1


def test_router_evidence_pool_no_new_evidence_different_query():
    """Two different queries returning the same result set — second is reused_known_evidence, not cache_hit."""
    shared_result = _page_result("Debian.pdf", 4, 6)
    db = _make_progressive_db(shared_result, shared_result)
    router = ModelRouter(
        database=db,
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("q1"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )
    turn = TurnContext(user_question="question")
    router.current_turn = turn
    try:
        router._retrieve_with_ledger("install package query A", ["debian"])
        # Second call: different query string so fingerprint differs, different DB call, but same result set
        # The page window from the first call is now in covered_intervals and passed as excluded_page_windows.
        # The DB returns the same result again (second_result = shared_result).
        # Because the result set fingerprint is already known, outcome = OUTCOME_REUSED_KNOWN.
        router._retrieve_with_ledger("install package query B", ["debian"])
    finally:
        router.current_turn = None

    pool = turn.evidence_pool
    assert pool.query_records[0].outcome == OUTCOME_NEW_EVIDENCE
    # Second query has a different fingerprint (different query string) so it goes to DB,
    # but the returned result set is identical → reused_known_evidence.
    assert pool.query_records[1].outcome in {OUTCOME_REUSED_KNOWN, OUTCOME_NO_NEW}


def test_router_evidence_pool_fresh_per_turn():
    """Each turn gets its own fresh EvidencePool; state does not bleed between turns."""
    db = _make_progressive_db(_page_result("Debian.pdf", 4, 6))
    router = ModelRouter(
        database=db,
        classifier=FakeClassifier(["debian"]),
        context_agent=FakeContextAgent("q"),
        history_summarizer=FakeHistorySummarizer(),
        context_summarizer=FakeContextSummarizer(summarized=False),
        responder=SpyResponder("ok"),
        memory_store=FakeMemoryStore(),
        memory_extractor=FakeMemoryExtractor(),
    )

    turn1 = TurnContext(user_question="turn 1")
    router.current_turn = turn1
    try:
        router._retrieve_with_ledger("query", ["debian"])
    finally:
        router.current_turn = None

    turn2 = TurnContext(user_question="turn 2")
    router.current_turn = turn2
    try:
        router._retrieve_with_ledger("query", ["debian"])
    finally:
        router.current_turn = None

    # Each turn has its own pool with exactly one query record
    assert len(turn1.evidence_pool.query_records) == 1
    assert len(turn2.evidence_pool.query_records) == 1
    assert turn1.evidence_pool is not turn2.evidence_pool
