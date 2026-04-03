import inspect
from dataclasses import dataclass, field
from enum import Enum, auto

from providers.anthropic_caller import AnthropicWorker
from providers.local_caller import LocalWorker
from agents.context_agent import Contextualizer
from providers.openAI_caller import OpenAIWorker
from agents.response_agent import ResponseAgent
from orchestration.history_preparer import PreparedHistory
from orchestration.routing_registry import get_allowed_labels, get_searchable_labels, get_skip_rag_labels
from orchestration.run_control import RunCancelledError, RunPausedError, invoke_cancel_check
from config.settings import SETTINGS
from agents.summarizers import ContextSummarizer, HistorySummarizer
from retrieval.vectorDB import VectorDB
from agents.classifier import Classifier
from agents.memory_extractor import MemoryExtractor
from agents.memory_resolver import MemoryResolver
from agents.magi import MagiSystem
from agents.magi.roles import MagiEager, MagiSkeptic, MagiHistorian
from agents.magi.arbiter import MagiArbiter


class RouterState(Enum):
    START = auto()
    LOAD_MEMORY = auto()
    SUMMARIZE_CONVERSATION_HISTORY = auto()
    CLASSIFY = auto()
    DECIDE_RAG = auto()
    REWRITE_QUERY = auto()
    RETRIEVE_CONTEXT = auto()
    GENERATE_RESPONSE = auto()
    SUMMARIZE_RETRIEVED_DOCS = auto()
    UPDATE_HISTORY = auto()
    DECIDE_MEMORY = auto()
    EXTRACT_MEMORY = auto()
    RESOLVE_MEMORY = auto()
    COMMIT_MEMORY = auto()
    AUTO_NAME = auto()
    DONE = auto()
    ERROR = auto()


class RouterExecutionError(RuntimeError):
    def __init__(self, message, turn=None):
        super().__init__(message)
        self.turn = turn


@dataclass
class TurnContext:
    user_question: str
    routing_labels: list = field(default_factory=list)
    suggested_search_labels: list = field(default_factory=list)
    retrieval_query: str = ""
    retrieved_docs: str = ""
    summarized_retrieved_docs: str = ""
    response: str = ""
    memory_snapshot_text: str = ""
    extracted_memory: dict | None = None
    memory_resolution: object | None = None
    summarized_conversation_history: object | None = None
    state_trace: list = field(default_factory=list)
    tool_events: list = field(default_factory=list)
    council_entries: list = field(default_factory=list)
    error: str | None = None
    persisted_user_message: object | None = None
    persisted_assistant_message: object | None = None
    schedule_auto_name: bool = False
    generated_chat_title: str = ""
    magi_resume_state: dict | None = None


class ModelRouter:
    WORKER_TYPES = {
        "openai": OpenAIWorker,
        "anthropic": AnthropicWorker,
        "local": LocalWorker,
    }

    def __init__(
        self,
        database=None,
        settings=None,
        classifier=None,
        context_agent=None,
        history_summarizer=None,
        context_summarizer=None,
        responder=None,
        chat_namer=None,
        response_tool_rounds=None,
        memory_store=None,
        memory_extractor=None,
        chat_store=None,
        chat_session_id=None,
        cancel_check=None,
        pause_check=None,
        persist_turn_messages=True,
        project_description="",
    ):
        self.project_description = project_description
        self.chat_store = chat_store
        self.chat_session_id = chat_session_id
        self.conversation_history = self._load_conversation_history(chat_store, chat_session_id)
        self.settings = settings or SETTINGS
        if response_tool_rounds is None:
            response_tool_rounds = self.settings.response_tool_rounds
        self.response_tool_rounds = response_tool_rounds
        self.database = database or VectorDB()
        if hasattr(self.database, "set_event_listener"):
            self.database.set_event_listener(self._emit_event)
        self.memory_store = self._build_memory_store(memory_store)
        self.memory_extractor = self._build_memory_extractor(
            memory_extractor,
            build_default=self.memory_store is not None,
        )
        self.cancel_check = cancel_check
        self.pause_check = pause_check
        self.persist_turn_messages = persist_turn_messages
        self.memory_resolver = MemoryResolver()
        self.current_state = RouterState.DONE
        self.current_turn = None
        self.last_turn = None
        self.state_listener = None
        self.event_listener = None
        self._stream_response_enabled = False
        self.classification_agent = self._build_classifier(classifier)
        self.context_agent = self._build_context_agent(context_agent)
        self.history_summarizer = self._build_history_summarizer(history_summarizer)
        self.context_summarizer = self._build_context_summarizer(context_summarizer)
        self.responder = self._build_responder(responder)
        self.chat_namer = self._build_worker(chat_namer, self.settings.chat_namer)
        self.magi_responder = None
        self.magi_lite_responder = None
        self._magi_active = "off"
        self.state_actions = {
            RouterState.START: self._start,
            RouterState.LOAD_MEMORY: self._load_memory,
            RouterState.SUMMARIZE_CONVERSATION_HISTORY: self._summarize_conversation_history,
            RouterState.CLASSIFY: self._classify,
            RouterState.DECIDE_RAG: self._decide_rag,
            RouterState.REWRITE_QUERY: self._rewrite_query,
            RouterState.RETRIEVE_CONTEXT: self._retrieve_context,
            RouterState.GENERATE_RESPONSE: self._generate_response,
            RouterState.SUMMARIZE_RETRIEVED_DOCS: self._summarize_retrieved_docs,
            RouterState.UPDATE_HISTORY: self._update_history,
            RouterState.DECIDE_MEMORY: self._decide_memory,
            RouterState.EXTRACT_MEMORY: self._extract_memory,
            RouterState.RESOLVE_MEMORY: self._resolve_memory,
            RouterState.COMMIT_MEMORY: self._commit_memory,
            RouterState.AUTO_NAME: self._auto_name,
        }

    def _load_conversation_history(self, chat_store, chat_session_id):
        if chat_store is None or not chat_session_id:
            return []
        return list(chat_store.load_conversation_history(chat_session_id))

    def _is_first_turn(self):
        """True when UPDATE_HISTORY has just appended the first user+assistant pair."""
        return len(self.conversation_history) == 2

    def _chat_session_has_title(self):
        if self.chat_store is None or not self.chat_session_id:
            return False
        get_chat_session = getattr(self.chat_store, "get_chat_session", None)
        if get_chat_session is None:
            return False
        try:
            chat_session = get_chat_session(self.chat_session_id)
        except Exception:
            return False
        return bool((getattr(chat_session, "title", "") or "").strip())

    def _next_post_turn_state(self, turn):
        if not self._is_first_turn():
            return RouterState.DONE
        if self._stream_response_enabled:
            turn.schedule_auto_name = True
            self._emit_event("auto_name_scheduled", {"mode": "follow_up"})
            return RouterState.DONE
        return RouterState.AUTO_NAME

    def ask_question(self, user_question, magi="off"):
        try:
            turn = self.run_turn(user_question, stream_response=False, magi=magi)
        except RouterExecutionError as exc:
            return f"Router error: {exc}"
        return turn.response

    def ask_question_stream(self, user_question, magi="off"):
        try:
            turn = self.run_turn(user_question, stream_response=True, magi=magi)
        except RouterExecutionError as exc:
            return f"Router error: {exc}"
        return turn.response

    def _execute_turn(self, turn, initial_state, *, stream_response=False, magi="off", manage_memory_turn=True):
        self._magi_active = magi
        self.current_turn = turn
        self._stream_response_enabled = stream_response
        state = initial_state
        self._set_state(state, turn)

        if manage_memory_turn and self.memory_store is not None:
            self.memory_store.begin_turn()
        try:
            while state not in {RouterState.DONE, RouterState.ERROR}:
                try:
                    self._check_cancel(f"before_state:{state.name}")
                    action = self.state_actions[state]
                    state = action(turn)
                    self._check_cancel(f"after_state:{state.name}")
                    self._set_state(state, turn)

                except (RunCancelledError, RunPausedError):
                    raise
                except Exception as exc:
                    turn.error = str(exc)
                    state = RouterState.ERROR
                    self._set_state(state, turn)
        finally:
            if manage_memory_turn and self.memory_store is not None:
                self.memory_store.end_turn()

        self.last_turn = turn
        self.current_turn = None
        self._stream_response_enabled = False
        if state == RouterState.ERROR:
            raise RouterExecutionError(turn.error or "Router error", turn=turn)

        return turn

    def run_turn(self, user_question, stream_response=False, magi=False):
        turn = TurnContext(user_question=user_question)
        return self._execute_turn(
            turn,
            RouterState.START,
            stream_response=stream_response,
            magi=magi,
            manage_memory_turn=True,
        )

    def run_magi_resumption(self, pause_state, *, stream_response=True, magi="full"):
        pause_state = dict(pause_state or {})
        history_payload = dict(pause_state.get("history") or {})
        turn = TurnContext(
            user_question=str(pause_state.get("user_query") or ""),
            retrieved_docs=str(pause_state.get("retrieved_docs") or ""),
            memory_snapshot_text=str(pause_state.get("memory_snapshot_text") or ""),
            summarized_conversation_history=PreparedHistory(
                recent_turns=list(history_payload.get("recent_turns") or []),
                summary_text=str(history_payload.get("summary_text") or ""),
            ),
            magi_resume_state=pause_state,
        )
        return self._execute_turn(
            turn,
            RouterState.GENERATE_RESPONSE,
            stream_response=stream_response,
            magi=magi,
            manage_memory_turn=True,
        )

    def run_auto_name_follow_up(self):
        self.conversation_history = self._load_conversation_history(self.chat_store, self.chat_session_id)
        turn = TurnContext(user_question="")
        return self._execute_turn(
            turn,
            RouterState.AUTO_NAME,
            stream_response=False,
            magi="off",
            manage_memory_turn=False,
        )

    def set_state_listener(self, listener):
        self.state_listener = listener

    def set_event_listener(self, listener):
        self.event_listener = listener

    def _set_state(self, state, turn):
        self.current_state = state
        turn.state_trace.append(state.name)
        if self.state_listener is not None:
            self.state_listener(state, turn)

    def _check_cancel(self, checkpoint):
        invoke_cancel_check(self.cancel_check, checkpoint)

    def _append_trace_marker(self, marker):
        if self.current_turn is not None:
            self.current_turn.state_trace.append(marker)

    def _emit_event(self, event_type, payload):
        if self.current_turn is not None:
            self.current_turn.tool_events.append({"type": event_type, "payload": payload})
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _auto_name_source_pair(self, turn):
        user_text = (turn.user_question or "").strip()
        assistant_text = (turn.response or "").strip()
        if user_text and assistant_text:
            return user_text, assistant_text

        first_user = ""
        first_assistant = ""
        for role, content in self.conversation_history:
            normalized_role = (role or "").strip().lower()
            if not first_user and normalized_role == "user":
                first_user = content or ""
                continue
            if first_user and normalized_role in {"assistant", "model"}:
                first_assistant = content or ""
                break
        return first_user.strip(), first_assistant.strip()

    def _summarize_extracted_memory(self, extracted, max_items=3):
        extracted = extracted or {}
        def summarize_fact(item):
            return f"{item.get('fact_key', '')}={item.get('fact_value', '')} [{item.get('source_type', 'model')}]"

        def summarize_issue(item):
            return f"{item.get('title', '')} [{item.get('status', 'unknown')}]"

        def summarize_attempt(item):
            parts = [item.get("action", ""), item.get("command", ""), item.get("outcome", "")]
            return " | ".join(part for part in parts if part)

        def summarize_constraint(item):
            return f"{item.get('constraint_key', '')}={item.get('constraint_value', '')}"

        def summarize_preference(item):
            return f"{item.get('preference_key', '')}={item.get('preference_value', '')}"

        return {
            "facts": [summarize_fact(item) for item in extracted.get("facts", [])[:max_items]],
            "issues": [summarize_issue(item) for item in extracted.get("issues", [])[:max_items]],
            "attempts": [summarize_attempt(item) for item in extracted.get("attempts", [])[:max_items]],
            "constraints": [summarize_constraint(item) for item in extracted.get("constraints", [])[:max_items]],
            "preferences": [summarize_preference(item) for item in extracted.get("preferences", [])[:max_items]],
        }

    def _handle_responder_state(self, state, payload):
        marker = f"RESPONDER_{state.name}"
        self._append_trace_marker(marker)
        self._emit_event(
            "responder_state",
            {
                "phase": "responder",
                "state": state.name,
                "details": payload or {},
                "trace_marker": marker,
            },
        )

    def _handle_magi_state(self, state, payload):
        marker = f"MAGI_{state.name}"
        self._append_trace_marker(marker)
        self._emit_event(
            "magi_state",
            {
                "phase": "magi",
                "state": state.name,
                "details": payload or {},
                "trace_marker": marker,
            },
        )

    def _instantiate_worker(self, provider, model, reasoning_effort=None):
        worker_class = self.WORKER_TYPES.get(provider.lower())
        if worker_class is None:
            raise ValueError(f"Unknown worker provider '{provider}'")
        if provider.lower() == "openai":
            if reasoning_effort not in {None, ""}:
                try:
                    signature = inspect.signature(worker_class)
                    if "reasoning_effort" in signature.parameters:
                        return worker_class(model=model, reasoning_effort=reasoning_effort)
                except (TypeError, ValueError):
                    pass
        return worker_class(model=model)

    def _default_model_for_provider(self, provider, fallback_model):
        return self.settings.provider_defaults.get(provider.lower(), fallback_model)

    def _build_worker(self, worker_spec, role_settings):
        if worker_spec is None:
            worker_spec = {
                "provider": role_settings.provider,
                "model": role_settings.model,
            }

        if isinstance(worker_spec, str):
            return self._instantiate_worker(
                worker_spec,
                self._default_model_for_provider(worker_spec, role_settings.model),
                role_settings.reasoning_effort,
            )

        if isinstance(worker_spec, dict):
            provider = worker_spec.get("provider", role_settings.provider)
            model = worker_spec.get("model")
            reasoning_effort = worker_spec.get("reasoning_effort", role_settings.reasoning_effort)
            if model is None:
                if provider.lower() == role_settings.provider.lower():
                    model = role_settings.model
                else:
                    model = self._default_model_for_provider(provider, role_settings.model)
            return self._instantiate_worker(provider, model, reasoning_effort)

        if hasattr(worker_spec, "generate_text"):
            return worker_spec

        raise TypeError("Worker spec must be a provider name, worker config dict, or worker instance.")

    def _build_classifier(self, classifier):
        if classifier is not None and hasattr(classifier, "call_api") and not hasattr(classifier, "generate_text"):
            return classifier
        return Classifier(
            worker=self._build_worker(classifier, self.settings.classifier),
            temperature=self.settings.classifier_temperature,
        )

    def _build_context_agent(self, context_agent):
        if context_agent is not None and hasattr(context_agent, "call_api") and not hasattr(context_agent, "generate_text"):
            return context_agent
        return Contextualizer(
            worker=self._build_worker(context_agent, self.settings.contextualizer),
            temperature=self.settings.contextualizer_temperature,
        )

    def _build_responder(self, responder):
        if responder is not None and hasattr(responder, "call_api") and not hasattr(responder, "generate_text"):
            return responder
        return ResponseAgent(
            worker=self._build_worker(responder, self.settings.responder),
            tools=self._build_response_tools(),
            tool_handler=self._handle_responder_tool_call,
            max_tool_rounds=self.response_tool_rounds,
            state_listener=self._handle_responder_state,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )

    def _build_magi_responder(self):
        tools = self._build_response_tools()
        tool_handler = self._handle_responder_tool_call
        tool_rounds = self.response_tool_rounds
        eager = MagiEager(
            worker=self._build_worker(None, self.settings.magi_eager),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        skeptic = MagiSkeptic(
            worker=self._build_worker(None, self.settings.magi_skeptic),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        historian = MagiHistorian(
            worker=self._build_worker(None, self.settings.magi_historian),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        arbiter = MagiArbiter(
            worker=self._build_worker(None, self.settings.magi_arbiter),
            tools=[], tool_handler=None, max_tool_rounds=0,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        return MagiSystem(
            eager=eager,
            skeptic=skeptic,
            historian=historian,
            arbiter=arbiter,
            max_discussion_rounds=self.settings.magi_max_discussion_rounds,
            state_listener=self._handle_magi_state,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
            pause_check=self.pause_check,
        )

    def _build_magi_lite_responder(self):
        tools = self._build_response_tools()
        tool_handler = self._handle_responder_tool_call
        tool_rounds = self.response_tool_rounds
        eager = MagiEager(
            worker=self._build_worker(None, self.settings.magi_lite_eager),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        skeptic = MagiSkeptic(
            worker=self._build_worker(None, self.settings.magi_lite_skeptic),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        historian = MagiHistorian(
            worker=self._build_worker(None, self.settings.magi_lite_historian),
            tools=tools, tool_handler=tool_handler, max_tool_rounds=tool_rounds,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        arbiter = MagiArbiter(
            worker=self._build_worker(None, self.settings.magi_lite_arbiter),
            tools=[], tool_handler=None, max_tool_rounds=0,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
        )
        return MagiSystem(
            eager=eager,
            skeptic=skeptic,
            historian=historian,
            arbiter=arbiter,
            max_discussion_rounds=self.settings.magi_lite_max_discussion_rounds,
            state_listener=self._handle_magi_state,
            event_listener=self._emit_event,
            cancel_check=self.cancel_check,
            pause_check=self.pause_check,
        )

    def _build_history_summarizer(self, history_summarizer):
        if history_summarizer is not None and hasattr(history_summarizer, "call_api") and not hasattr(history_summarizer, "generate_text"):
            return history_summarizer
        return HistorySummarizer(
            worker=self._build_worker(history_summarizer, self.settings.history_summarizer),
            temperature=self.settings.history_summarizer_temperature,
            max_recent_turns=self.settings.history_max_recent_turns,
            summarize_turn_threshold=self.settings.history_summarize_turn_threshold,
            summarize_char_threshold=self.settings.history_summarize_char_threshold,
        )

    def _build_context_summarizer(self, context_summarizer):
        if context_summarizer is not None and hasattr(context_summarizer, "call_api") and not hasattr(context_summarizer, "generate_text"):
            return context_summarizer
        return ContextSummarizer(worker=self._build_worker(context_summarizer, self.settings.context_summarizer))

    def _build_response_tools(self):
        allowed_labels = get_allowed_labels()
        tools = [
            {
                "name": "search_rag_database",
                "description": (
                    "Search the RAG database for relevant manual context. "
                    "Use relevant_documents as suggested domains to scope the search when possible. "
                    "If the best search path is unclear, you may search broadly."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query or question to look up",
                        },
                        "relevant_documents": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": allowed_labels,
                            },
                            "description": "Suggested domain labels to bias or narrow the search",
                        },
                    },
                    "required": ["query", "relevant_documents"],
                },
            },
            {
                "name": "search_conversation_history",
                "description": (
                    "Search the raw uncompressed conversation history for prior user configuration, "
                    "attempted fixes, errors, or assistant guidance."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Phrase or keywords to search for in prior conversation",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of matching conversation snippets to return",
                        },
                    },
                    "required": ["query", "max_results"],
                },
            },
        ]
        if self.memory_store is not None:
            tools.extend(
                [
                    {
                        "name": "get_system_profile",
                        "description": "Return the assistant's currently remembered system profile for the local host.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {},
                            "required": [],
                        },
                    },
                    {
                        "name": "search_memory_issues",
                        "description": "Search structured memory for prior or active issues relevant to the current problem.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Keywords describing the issue to search for",
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "Maximum number of memory issue matches to return",
                                },
                            },
                            "required": ["query", "max_results"],
                        },
                    },
                    {
                        "name": "search_attempt_log",
                        "description": "Search structured memory for past attempted fixes, commands, or configuration changes.",
                        "parameters": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Keywords describing the attempted solution to search for",
                                },
                                "max_results": {
                                    "type": "integer",
                                    "description": "Maximum number of matching attempts to return",
                                },
                            },
                            "required": ["query", "max_results"],
                        },
                    },
                ]
            )
        return tools

    def _searchable_labels(self, labels):
        return get_searchable_labels(labels or [])

    def _build_memory_extractor(self, memory_extractor, build_default=False):
        if memory_extractor is not None and hasattr(memory_extractor, "call_api") and not hasattr(memory_extractor, "generate_text"):
            return memory_extractor
        if memory_extractor is None and not build_default:
            return None
        return MemoryExtractor(worker=self._build_worker(memory_extractor, self.settings.memory_extractor))

    def _build_memory_store(self, memory_store):
        return memory_store

    def _handle_responder_tool_call(self, tool_name, tool_args):
        self._check_cancel(f"before_tool:{tool_name}")
        self._emit_event("tool_start", {"name": tool_name, "args": tool_args})

        try:
            if tool_name in {"search_rag_database", "search_RAG_database"}:
                self._append_trace_marker("TOOL_RETRIEVE_CONTEXT")
                query = tool_args.get("query")
                relevant_documents = tool_args.get("relevant_documents")
                if query is None or relevant_documents is None:
                    result = {"error": "missing required arguments"}
                else:
                    result = self.database.retrieve_context(
                        query,
                        self._searchable_labels(relevant_documents),
                    )
            elif tool_name == "search_conversation_history":
                self._append_trace_marker("TOOL_SEARCH_HISTORY")
                result = self._search_conversation_history(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
            elif tool_name == "get_system_profile":
                result = self.memory_store.format_system_profile()
            elif tool_name == "search_memory_issues":
                result = self.memory_store.search_issues(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
            elif tool_name == "search_attempt_log":
                result = self.memory_store.search_attempts(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
            else:
                result = {"error": f"unknown tool '{tool_name}'"}
        except Exception as exc:
            result = {"error": str(exc)}

        result_size = len(result) if isinstance(result, str) else len(str(result))
        if isinstance(result, dict) and "error" in result:
            self._emit_event(
                "tool_error",
                {"name": tool_name, "error": result["error"]},
            )
        else:
            self._emit_event(
                "tool_complete",
                {"name": tool_name, "result_size": result_size},
            )
        self._check_cancel(f"after_tool:{tool_name}")
        return result

    def _search_conversation_history(self, query, max_results=5):
        query = (query or "").strip().lower()
        if not query:
            return ""

        try:
            max_results = max(1, min(int(max_results), 8))
        except Exception:
            max_results = 5

        query_terms = [term for term in query.split() if term]
        scored = []
        for idx, item in enumerate(self.conversation_history):
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            role, content = item
            haystack = (content or "").lower()
            if not haystack:
                continue
            score = 0
            if query in haystack:
                score += 5
            for term in query_terms:
                if term in haystack:
                    score += 1
            if score:
                scored.append((score, idx, role, content))

        if not scored:
            return ""

        scored.sort(reverse=True)
        lines = []
        for _, idx, role, content in scored[:max_results]:
            display_role = "User" if role == "user" else "Model"
            snippet = " ".join((content or "").split())
            if len(snippet) > 320:
                snippet = snippet[:317].rstrip() + "..."
            lines.append(f"[Turn {idx + 1} | {display_role}] {snippet}")
        return "\n".join(lines)

    def _should_skip_rag(self, routing_labels):
        return not self._searchable_labels(routing_labels)

    def _start(self, turn):
        if self.memory_store is None:
            return RouterState.SUMMARIZE_CONVERSATION_HISTORY
        return RouterState.LOAD_MEMORY

    def _load_memory(self, turn):
        if self.memory_store is not None:
            turn.memory_snapshot_text = self.memory_store.format_memory_snapshot(turn.user_question)
            self._emit_event(
                "memory_loaded",
                {
                    "chars": len(turn.memory_snapshot_text or ""),
                    "has_memory": bool(turn.memory_snapshot_text.strip()),
                },
            )
        desc = (self.project_description or "").strip()
        if desc:
            prefix = f"PROJECT DESCRIPTION:\n{desc}\n\n"
            turn.memory_snapshot_text = prefix + (turn.memory_snapshot_text or "")
        return RouterState.SUMMARIZE_CONVERSATION_HISTORY

    # Caveat: this state may intentionally no-op when the conversation is small
    # enough that summarizing would add cost without reducing meaningful context.
    def _summarize_conversation_history(self, turn):
        turn.summarized_conversation_history, summarized = self.history_summarizer.call_api(
            self.conversation_history,
        )
        self._emit_event(
            "summarized_conversation_history",
            {
                "recent_turns": len(turn.summarized_conversation_history.recent_turns),
                "summary_chars": len(turn.summarized_conversation_history.summary_text or ""),
                "summarized": summarized,
            },
        )
        return RouterState.CLASSIFY

    def _classify(self, turn):
        turn.routing_labels = self.classification_agent.call_api(
            turn.user_question,
            turn.summarized_conversation_history,
            turn.memory_snapshot_text,
        ) or []
        turn.suggested_search_labels = self._searchable_labels(turn.routing_labels)
        return RouterState.DECIDE_RAG

    def _rewrite_query(self, turn):
        turn.retrieval_query = self.context_agent.call_api(
            turn.user_question,
            turn.summarized_conversation_history.recent_turns if turn.summarized_conversation_history else [],
        )
        return RouterState.RETRIEVE_CONTEXT

    def _decide_rag(self, turn):
        if self._should_skip_rag(turn.routing_labels):
            turn.retrieved_docs = ""
            return RouterState.GENERATE_RESPONSE
        return RouterState.REWRITE_QUERY

    def _retrieve_context(self, turn):
        turn.retrieved_docs = self.database.retrieve_context(
            turn.retrieval_query,
            turn.suggested_search_labels,
        )
        return RouterState.GENERATE_RESPONSE

    def _summarize_retrieved_docs(self, turn):
        turn.summarized_retrieved_docs, summarized = self.context_summarizer.call_api(
            turn.user_question,
            turn.retrieved_docs,
        )
        self._emit_event(
            "summarized_retrieved_docs",
            {
                "raw_chars": len(turn.retrieved_docs or ""),
                "summary_chars": len(turn.summarized_retrieved_docs or ""),
                "summarized": summarized,
            },
        )
        return RouterState.UPDATE_HISTORY

    def _generate_response(self, turn):
        self._check_cancel("before_generate_response")
        if self._magi_active == "full":
            if self.magi_responder is None:
                self.magi_responder = self._build_magi_responder()
            responder = self.magi_responder
        elif self._magi_active == "lite":
            if self.magi_lite_responder is None:
                self.magi_lite_responder = self._build_magi_lite_responder()
            responder = self.magi_lite_responder
        else:
            responder = self.responder
        if turn.magi_resume_state and hasattr(responder, "resume_api"):
            turn.response = responder.resume_api(
                turn.user_question,
                turn.retrieved_docs,
                turn.summarized_conversation_history,
                turn.memory_snapshot_text,
                pause_state=turn.magi_resume_state,
                stream=self._stream_response_enabled,
            )
        else:
            responder_method = responder.stream_api if self._stream_response_enabled else responder.call_api
            turn.response = responder_method(
                turn.user_question,
                turn.retrieved_docs,
                turn.summarized_conversation_history,
                turn.memory_snapshot_text,
            )
        self._check_cancel("after_generate_response")
        turn.council_entries = list(getattr(responder, "last_council_entries", None) or [])
        if not (turn.retrieved_docs or "").strip():
            return RouterState.UPDATE_HISTORY
        return RouterState.SUMMARIZE_RETRIEVED_DOCS

    def _update_history(self, turn):
        self._check_cancel("before_update_history")
        turn.persisted_user_message = self.update_history("user", turn.user_question)
        turn.persisted_assistant_message = self.update_history("model", turn.response, council_entries=turn.council_entries or None)
        return RouterState.DECIDE_MEMORY

    def _decide_memory(self, turn):
        if self.memory_store is None or self.memory_extractor is None:
            self._emit_event("memory_skipped", {"reason": "missing_store_or_extractor"})
            return self._next_post_turn_state(turn)
        return RouterState.EXTRACT_MEMORY

    def _extract_memory(self, turn):
        if self.memory_store is None or self.memory_extractor is None:
            self._emit_event("memory_skipped", {"reason": "missing_store_or_extractor"})
            turn.extracted_memory = None
            return RouterState.RESOLVE_MEMORY

        try:
            turn.extracted_memory = self.memory_extractor.call_api(
                turn.user_question,
                turn.response,
                recent_history=self.conversation_history[-6:],
            )
            extracted = turn.extracted_memory or {}
            self._emit_event(
                "memory_extracted",
                {
                    "facts": len(extracted.get("facts", [])),
                    "issues": len(extracted.get("issues", [])),
                    "attempts": len(extracted.get("attempts", [])),
                    "constraints": len(extracted.get("constraints", [])),
                    "preferences": len(extracted.get("preferences", [])),
                    "has_session_summary": bool((extracted.get("session_summary") or "").strip()),
                    "examples": self._summarize_extracted_memory(extracted),
                },
            )
        except Exception as exc:
            turn.extracted_memory = None
            self._emit_event("memory_error", {"phase": "extract", "error": str(exc)})
        return RouterState.RESOLVE_MEMORY

    def _resolve_memory(self, turn):
        if self.memory_store is None or turn.extracted_memory is None:
            turn.memory_resolution = None
            self._emit_event("memory_resolved", {"skipped": True})
            return RouterState.COMMIT_MEMORY

        try:
            snapshot = self.memory_store.load_snapshot()
            turn.memory_resolution = self.memory_resolver.resolve(turn.extracted_memory, snapshot=snapshot)
            self._emit_event("memory_resolved", turn.memory_resolution.details())
        except Exception as exc:
            turn.memory_resolution = None
            self._emit_event("memory_error", {"phase": "resolve", "error": str(exc)})
        return RouterState.COMMIT_MEMORY

    def _commit_memory(self, turn):
        if self.memory_store is None or turn.memory_resolution is None:
            return self._next_post_turn_state(turn)

        try:
            self.memory_store.commit_resolution(
                turn.memory_resolution,
                user_question=turn.user_question,
                assistant_response=turn.response,
            )
            self._emit_event("memory_committed", turn.memory_resolution.details())
        except Exception as exc:
            self._emit_event("memory_error", {"phase": "commit", "error": str(exc)})
        return self._next_post_turn_state(turn)

    def _auto_name(self, turn):
        if not (self.chat_store and self.chat_session_id):
            self._emit_event("chat_name_skipped", {"reason": "missing_chat_context"})
            return RouterState.DONE
        if not hasattr(self.chat_store, "update_chat_session_title"):
            self._emit_event("chat_name_skipped", {"reason": "missing_title_store"})
            return RouterState.DONE
        if self._chat_session_has_title():
            self._emit_event("chat_name_skipped", {"reason": "already_titled"})
            return RouterState.DONE

        user_text, assistant_text = self._auto_name_source_pair(turn)
        if not user_text or not assistant_text:
            self._emit_event("chat_name_skipped", {"reason": "missing_opening_exchange"})
            return RouterState.DONE

        try:
            self._check_cancel("before_auto_name")
            raw_title = self.chat_namer.generate_text(
                (
                    "You generate short chat titles from the opening exchange. "
                    "Return only a concise 3 to 6 word title with no quotes or trailing punctuation."
                ),
                f"User: {user_text[:400]}\nAssistant: {assistant_text[:400]}",
                temperature=0.3,
                max_output_tokens=30,
                cancel_check=self.cancel_check,
            )
            self._check_cancel("after_auto_name")
            title = (raw_title or "").strip().strip('"').strip("'").strip()[:80].rstrip(".,;:!?")
            if title:
                turn.generated_chat_title = title
                self.chat_store.update_chat_session_title(self.chat_session_id, title)
                self._emit_event("chat_named", {"title": title})
            else:
                self._emit_event("chat_name_skipped", {"reason": "blank_title"})
        except RunCancelledError:
            raise
        except Exception as exc:
            self._emit_event("chat_name_error", {"error": str(exc)})
        return RouterState.DONE

    def update_history(self, role, content, council_entries=None):
        self.conversation_history.append((role, content))
        if self.persist_turn_messages and self.chat_store is not None and self.chat_session_id:
            append_fn = getattr(self.chat_store, "append_message_fast", self.chat_store.append_message)
            return append_fn(self.chat_session_id, role, content, council_entries=council_entries)
        return None

    def get_history(self):
        return self.conversation_history

    def getDB(self):
        return self.database
