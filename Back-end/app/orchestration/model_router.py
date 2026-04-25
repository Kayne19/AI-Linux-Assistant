import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto

from ingestion.identity.vocabularies import (
    InitSystem,
    MajorSubsystem,
    OsFamily,
    PackageManager,
    SourceFamily,
)
from providers.anthropic_caller import AnthropicWorker
from providers.google_caller import GoogleWorker
from providers.local_caller import LocalWorker
from agents.context_agent import Contextualizer
from providers.openAI_caller import OpenAIWorker
from agents.response_agent import ResponseAgent, ResponseState
from orchestration.history_preparer import PreparedHistory
from orchestration.routing_registry import get_allowed_labels, get_searchable_labels
from orchestration.run_control import RunCancelledError, RunPausedError, invoke_cancel_check
from orchestration.evidence_pool import (
    ALLOWED_GAP_TYPES,
    ALLOWED_REPEAT_REASONS,
    GENERIC_EVIDENCE_GOALS,
    GATE_BLOCK,
    GATE_REQUIRE_REASON,
    EvidencePool,
    normalize_evidence_goal,
    normalize_gap_type,
    normalize_repeat_reason,
)
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
    retrieved_context_blocks: list = field(default_factory=list)
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
    evidence_pool: object | None = None  # EvidencePool, created on first use


class ModelRouter:
    WORKER_TYPES = {
        "openai": OpenAIWorker,
        "anthropic": AnthropicWorker,
        "google": GoogleWorker,
        "local": LocalWorker,
    }
    RESPONDER_DECISION_TOOL_NAME = "responder_decide_next_step"
    RESPONDER_EVALUATION_TOOL_NAME = "responder_evaluate_tool_result"

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
        self._known_canonical_doc_ids_cache = None
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
        self._standalone_evidence_pool = EvidencePool()
        # Tracks active MAGI role/phase/round so tool calls can be tagged
        self._active_magi_caller: dict = {}
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
        self._active_magi_caller = {}
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
            retrieved_context_blocks=list(pause_state.get("retrieved_context_blocks") or []),
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

    def _regular_responder_supports_router_protocol(self, responder):
        return isinstance(responder, ResponseAgent) and responder.supports_router_protocol()

    def _build_responder_decision_tool(self):
        allowed_labels = get_allowed_labels()
        return {
            "name": self.RESPONDER_DECISION_TOOL_NAME,
            "description": (
                "Router-owned responder control tool. Call exactly once to choose the next regular-responder action "
                "before any further retrieval."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["answer_now", "ask_focused_follow_up_questions", "search"],
                        "description": "Exactly one next action for the router-owned responder protocol.",
                    },
                    "unresolved_gap": {
                        "type": "string",
                        "description": "The specific missing point that is blocking a grounded response.",
                    },
                    "gap_type": {
                        "type": "string",
                        "enum": sorted(ALLOWED_GAP_TYPES),
                        "description": "Optional hint about whether the gap is procedural, environmental, or confirmatory.",
                    },
                    "why_current_evidence_is_insufficient": {
                        "type": "string",
                        "description": "Brief explanation of why the current evidence cannot safely answer yet.",
                    },
                    "requested_evidence_goal": {
                        "type": "string",
                        "description": "Internal evidence goal for a search decision.",
                    },
                    "repeat_reason": {
                        "type": "string",
                        "enum": sorted(ALLOWED_REPEAT_REASONS),
                        "description": "Required for repeated same-scope retrieval.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Concrete retrieval query to execute when action=search.",
                    },
                    "relevant_documents": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": allowed_labels,
                        },
                        "description": "Suggested domain labels to bias or narrow the search.",
                    },
                    "scope_hints": self._scope_hint_schema(),
                    "canonical_source_ids": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": self._known_canonical_doc_ids(),
                        },
                        "description": "Optional canonical document IDs to pin the search to specific documents.",
                    },
                    "focused_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 3,
                        "description": "One to three tightly related follow-up questions when action=ask_focused_follow_up_questions.",
                    },
                },
                "required": ["action"],
            },
        }

    def _build_responder_evaluation_tool(self):
        return {
            "name": self.RESPONDER_EVALUATION_TOOL_NAME,
            "description": (
                "Router-owned responder evaluation tool. Call exactly once after a router-executed search result to "
                "state what changed and whether another search is still justified."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "new_fact_or_evidence": {
                        "type": "string",
                        "description": "What new fact or evidence the search added. Use 'none' if nothing material was added.",
                    },
                    "reduced_unresolved_gap": {
                        "type": "string",
                        "description": "Which unresolved gap the search reduced, if any.",
                    },
                    "progress_assessment": {
                        "type": "string",
                        "enum": ["meaningful_progress", "partial_progress", "no_meaningful_progress"],
                        "description": "Whether the search materially advanced the active gap.",
                    },
                    "another_search_justified": {
                        "type": "boolean",
                        "description": "Whether another search is still justified after this result.",
                    },
                    "remaining_unresolved_gap": {
                        "type": "string",
                        "description": "Any unresolved gap that remains after the search result.",
                    },
                    "gap_type": {
                        "type": "string",
                        "enum": sorted(ALLOWED_GAP_TYPES),
                        "description": "Optional hint for the remaining unresolved gap.",
                    },
                    "suggested_repeat_reason": {
                        "type": "string",
                        "enum": sorted(ALLOWED_REPEAT_REASONS),
                        "description": "Optional repeat reason if another same-scope search is justified.",
                    },
                },
                "required": [
                    "new_fact_or_evidence",
                    "reduced_unresolved_gap",
                    "progress_assessment",
                    "another_search_justified",
                ],
            },
        }

    def _responder_decision_prompt(self, responder):
        return (
            f"{responder.chatbot_prompt}\n\n"
            "ROUTER-OWNED REGULAR RESPONDER PROTOCOL\n"
            "- You are in RESPONDER_DECIDE_NEXT_STEP.\n"
            f"- Call `{self.RESPONDER_DECISION_TOOL_NAME}` exactly once.\n"
            "- Do not answer the user directly in this phase.\n"
            "- Choose exactly one action: answer_now, ask_focused_follow_up_questions, or search.\n"
            "- Before any retrieval, name the unresolved gap and why current evidence is insufficient.\n"
            "- Repeated same-scope search requires a named repeat_reason.\n"
            "- Prefer ask_focused_follow_up_questions when the missing information is mainly about the user's actual environment or setup.\n"
            "- If you ask follow-up questions, keep them tightly related to one unresolved gap and limit them to 1 to 3 questions.\n"
        )

    def _responder_evaluation_prompt(self, responder):
        return (
            f"{responder.chatbot_prompt}\n\n"
            "ROUTER-OWNED REGULAR RESPONDER PROTOCOL\n"
            "- You are in RESPONDER_EVALUATE_TOOL_RESULT.\n"
            f"- Call `{self.RESPONDER_EVALUATION_TOOL_NAME}` exactly once.\n"
            "- Do not answer the user directly in this phase.\n"
            "- State what new fact or evidence the search added, which unresolved gap it reduced if any, and whether another search is still justified.\n"
            "- Use no_meaningful_progress when the search did not materially advance the gap.\n"
        )

    def _responder_finalize_prompt(self, responder, *, reason="", question_limit=None):
        extra = ""
        if reason:
            extra = f"- Finalization reason: {reason}.\n"
        question_note = ""
        if question_limit is not None:
            question_note = (
                f"- If you need follow-up questions, ask at most {int(question_limit)} tightly related questions about one unresolved gap.\n"
                "- Do not broaden into a questionnaire or unrelated list.\n"
            )
        return (
            f"{responder.chatbot_prompt}\n\n"
            "ROUTER-OWNED REGULAR RESPONDER PROTOCOL\n"
            "- You are in RESPONDER_FINALIZE_RESPONSE.\n"
            "- No tools are available.\n"
            "- Finalize using only the evidence already present in the conversation and prior tool results.\n"
            f"{extra}"
            f"{question_note}"
        )

    def _extract_expected_protocol_tool_call(self, step_result, expected_tool_name):
        tool_calls = list(getattr(step_result, "tool_calls", []) or [])
        if len(tool_calls) != 1:
            return None, f"expected exactly one tool call, received {len(tool_calls)}"
        tool_call = tool_calls[0]
        if tool_call.name != expected_tool_name:
            return None, f"expected {expected_tool_name}, received {tool_call.name}"
        return tool_call, ""

    def _protocol_tool_result(self, tool_call, output):
        return {
            "call_id": tool_call.call_id,
            "name": tool_call.name,
            "arguments": dict(tool_call.arguments or {}),
            "output": output,
        }

    def _known_canonical_doc_ids(self):
        if self._known_canonical_doc_ids_cache is not None:
            return list(self._known_canonical_doc_ids_cache)

        known_doc_ids = getattr(self.database, "known_canonical_doc_ids", None)
        if callable(known_doc_ids):
            try:
                self._known_canonical_doc_ids_cache = sorted(
                    {str(doc_id) for doc_id in known_doc_ids() if doc_id}
                )
                return list(self._known_canonical_doc_ids_cache)
            except Exception:
                pass

        documents_store = getattr(self.database, "documents_store", None)
        if documents_store is None:
            self._known_canonical_doc_ids_cache = []
            return []
        try:
            rows = documents_store.load_documents()
        except Exception:
            self._known_canonical_doc_ids_cache = []
            return []
        self._known_canonical_doc_ids_cache = sorted(
            {
                str(row.get("canonical_source_id"))
                for row in rows
                if isinstance(row, dict) and row.get("canonical_source_id")
            }
        )
        return list(self._known_canonical_doc_ids_cache)

    @staticmethod
    def _scope_hint_schema():
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "os_family": {
                    "type": "string",
                    "enum": [member.value for member in OsFamily],
                    "description": "Operating-system family the question targets.",
                },
                "source_family": {
                    "type": "string",
                    "enum": [member.value for member in SourceFamily],
                    "description": "Document family, such as debian, proxmox, or arch.",
                },
                "package_managers": {
                    "type": "array",
                    "items": {"type": "string", "enum": [member.value for member in PackageManager]},
                    "description": "Package managers relevant to the question.",
                },
                "init_systems": {
                    "type": "array",
                    "items": {"type": "string", "enum": [member.value for member in InitSystem]},
                    "description": "Init systems relevant to the question.",
                },
                "major_subsystems": {
                    "type": "array",
                    "items": {"type": "string", "enum": [member.value for member in MajorSubsystem]},
                    "description": "Major subsystems the question targets.",
                },
            },
        }

    def _validated_scope_hints(self, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            return None

        scalar_fields = {
            "os_family": {member.value for member in OsFamily},
            "source_family": {member.value for member in SourceFamily},
        }
        list_fields = {
            "package_managers": {member.value for member in PackageManager},
            "init_systems": {member.value for member in InitSystem},
            "major_subsystems": {member.value for member in MajorSubsystem},
        }
        cleaned = {}

        for field_name, allowed in scalar_fields.items():
            field_value = value.get(field_name)
            if isinstance(field_value, str) and field_value in allowed:
                cleaned[field_name] = field_value

        for field_name, allowed in list_fields.items():
            field_value = value.get(field_name)
            if not isinstance(field_value, list):
                continue
            valid_values = [item for item in field_value if isinstance(item, str) and item in allowed]
            if valid_values:
                cleaned[field_name] = valid_values

        return cleaned or None

    def _validated_canonical_doc_ids(self, value):
        if value is None:
            return ()
        if not isinstance(value, list):
            return ()
        known_ids = set(self._known_canonical_doc_ids())
        if not known_ids:
            return ()
        return tuple(item for item in value if isinstance(item, str) and item in known_ids)

    def _validated_relevant_documents(self, relevant_documents):
        if relevant_documents is None:
            return []
        if not isinstance(relevant_documents, list):
            return None
        allowed = set(get_allowed_labels())
        validated = []
        for item in relevant_documents:
            if not isinstance(item, str) or item not in allowed:
                return None
            validated.append(item)
        return validated

    def _validate_responder_decision(self, tool_call):
        args = dict(getattr(tool_call, "arguments", {}) or {})
        action = str(args.get("action") or "").strip()
        if action not in {"answer_now", "ask_focused_follow_up_questions", "search"}:
            return None, "missing or invalid action"

        decision = {
            "action": action,
            "unresolved_gap": str(args.get("unresolved_gap") or "").strip(),
            "gap_type": normalize_gap_type(args.get("gap_type", "")),
            "why_current_evidence_is_insufficient": str(args.get("why_current_evidence_is_insufficient") or "").strip(),
            "requested_evidence_goal": normalize_evidence_goal(args.get("requested_evidence_goal", "")),
            "repeat_reason": normalize_repeat_reason(args.get("repeat_reason", "")),
            "query": str(args.get("query") or "").strip(),
            "relevant_documents": self._validated_relevant_documents(args.get("relevant_documents")),
            "scope_hints": self._validated_scope_hints(args.get("scope_hints")),
            "canonical_source_ids": self._validated_canonical_doc_ids(args.get("canonical_source_ids")),
            "focused_questions": [
                str(question or "").strip()
                for question in list(args.get("focused_questions") or [])
                if str(question or "").strip()
            ],
        }
        if decision["relevant_documents"] is None:
            return None, "invalid relevant_documents"

        if action == "search":
            if not decision["unresolved_gap"]:
                return None, "search requires unresolved_gap"
            if not decision["why_current_evidence_is_insufficient"]:
                return None, "search requires why_current_evidence_is_insufficient"
            if not decision["requested_evidence_goal"]:
                return None, "search requires requested_evidence_goal"
            if not decision["query"]:
                return None, "search requires query"
        elif action == "ask_focused_follow_up_questions":
            if not decision["unresolved_gap"]:
                return None, "follow-up questions require unresolved_gap"
            if not 1 <= len(decision["focused_questions"]) <= 3:
                return None, "follow-up questions must contain 1 to 3 entries"

        return decision, ""

    def _validate_responder_evaluation(self, tool_call):
        args = dict(getattr(tool_call, "arguments", {}) or {})
        progress_assessment = str(args.get("progress_assessment") or "").strip()
        if progress_assessment not in {"meaningful_progress", "partial_progress", "no_meaningful_progress"}:
            return None, "missing or invalid progress_assessment"
        another_search_justified = args.get("another_search_justified")
        if not isinstance(another_search_justified, bool):
            return None, "another_search_justified must be boolean"
        evaluation = {
            "new_fact_or_evidence": str(args.get("new_fact_or_evidence") or "").strip(),
            "reduced_unresolved_gap": str(args.get("reduced_unresolved_gap") or "").strip(),
            "progress_assessment": progress_assessment,
            "another_search_justified": another_search_justified,
            "remaining_unresolved_gap": str(args.get("remaining_unresolved_gap") or "").strip(),
            "gap_type": normalize_gap_type(args.get("gap_type", "")),
            "suggested_repeat_reason": normalize_repeat_reason(args.get("suggested_repeat_reason", "")),
        }
        if not evaluation["new_fact_or_evidence"]:
            return None, "evaluation requires new_fact_or_evidence"
        if "reduced_unresolved_gap" not in args:
            return None, "evaluation requires reduced_unresolved_gap"
        return evaluation, ""

    def _decision_prefers_follow_up_questions(self, decision):
        if decision.get("gap_type") == "environment_fact_gap":
            return True
        gap_text = f"{decision.get('unresolved_gap', '')} {decision.get('why_current_evidence_is_insufficient', '')}".lower()
        environment_tokens = (
            "lan",
            "public internet",
            "dhcp",
            "static ip",
            "bridge",
            "interface",
            "dns",
            "domain",
            "hostname",
            "actual setup",
            "environment",
            "target host",
        )
        return any(token in gap_text for token in environment_tokens)

    def _execute_regular_responder_search(self, decision):
        query = decision.get("query") or ""
        relevant_documents = list(decision.get("relevant_documents") or [])
        scope_hints = decision.get("scope_hints")
        explicit_doc_ids = tuple(decision.get("canonical_source_ids") or ())
        tool_args = {
            "query": query,
            "relevant_documents": relevant_documents,
            "repeat_reason": decision.get("repeat_reason", ""),
            "requested_evidence_goal": decision.get("requested_evidence_goal", ""),
            "gap_type": decision.get("gap_type", ""),
            "unresolved_gap": decision.get("unresolved_gap", ""),
        }
        if scope_hints:
            tool_args["scope_hints"] = scope_hints
        if explicit_doc_ids:
            tool_args["canonical_source_ids"] = list(explicit_doc_ids)
        self._check_cancel("before_tool:search_rag_database")
        self._emit_event("tool_start", {"name": "search_rag_database", "args": tool_args})
        self._append_trace_marker("TOOL_RETRIEVE_CONTEXT")
        try:
            searchable_labels = self._searchable_labels(relevant_documents)
            retrieval_result, cached_hit = self._retrieve_with_ledger(
                query,
                searchable_labels,
                repeat_reason=decision.get("repeat_reason", ""),
                requested_evidence_goal=decision.get("requested_evidence_goal", ""),
                unresolved_issue=decision.get("unresolved_gap", ""),
                gap_type=decision.get("gap_type", ""),
                strict_repeat_reason=True,
                router_hint=scope_hints,
                explicit_doc_ids=explicit_doc_ids,
            )
            retrieval_metadata = dict(retrieval_result.get("retrieval_metadata") or {})
            search_text = str(retrieval_result.get("context_text") or "")
            pool = self._active_evidence_pool()
            tool_complete_payload = {
                "name": "search_rag_database",
                "result_size": len(search_text),
                "result_text": search_text,
                "result_blocks": list(retrieval_result.get("merged_blocks") or []),
                "selected_sources": list(retrieval_result.get("selected_sources") or []),
                "cached": cached_hit,
                "anchor_count": retrieval_metadata.get("anchor_count", 0),
                "anchor_pages": list(retrieval_metadata.get("anchor_pages") or []),
                "fetched_neighbor_pages": list(retrieval_metadata.get("fetched_neighbor_pages") or []),
                "delivered_bundle_count": retrieval_metadata.get("delivered_bundle_count", 0),
                "excluded_seen_count": retrieval_metadata.get("excluded_seen_count", 0),
                "skipped_bundle_count": retrieval_metadata.get("skipped_bundle_count", 0),
                "gate_action": retrieval_metadata.get("gate_action", ""),
                "search_outcome": retrieval_metadata.get("search_outcome") or pool.last_query_outcome(),
                "usefulness": retrieval_metadata.get("usefulness") or pool.last_query_usefulness(),
                "usefulness_reason": retrieval_metadata.get("usefulness_reason", ""),
                "scope_key": retrieval_metadata.get("scope_key") or pool.last_query_scope_key(),
                "covered_region_count": len(pool.known_covered_region_keys()),
                "scope_exhausted": bool(pool.scope_state.exhausted_scope_keys),
                "soft_exhausted_scope_keys": sorted(pool.scope_state.soft_exhausted_scope_keys),
                "hard_exhausted_scope_keys": sorted(pool.scope_state.hard_exhausted_scope_keys),
                "requested_evidence_goal": retrieval_metadata.get("requested_evidence_goal", decision.get("requested_evidence_goal", "")),
                "gap_type": retrieval_metadata.get("gap_type", decision.get("gap_type", "")),
                "repeat_reason": retrieval_metadata.get("repeat_reason", decision.get("repeat_reason", "")),
            }
            self._emit_event("tool_complete", tool_complete_payload)
        except Exception as exc:
            self._emit_event("tool_error", {"name": "search_rag_database", "error": str(exc)})
            self._check_cancel("after_tool:search_rag_database")
            raise
        self._check_cancel("after_tool:search_rag_database")
        payload = {
            "decision": dict(decision),
            "search_result_text": search_text,
            "selected_sources": list(retrieval_result.get("selected_sources") or []),
            "result_blocks": list(retrieval_result.get("merged_blocks") or []),
            "cached": cached_hit,
            "retrieval_metadata": retrieval_metadata,
        }
        return payload, retrieval_metadata

    def _finalize_regular_responder(self, responder, turn, session_state, tool_results, *, reason, round_number, question_limit=None):
        responder.emit_state(
            ResponseState.FINALIZE_RESPONSE,
            {
                "round": round_number,
                "reason": reason,
            },
        )
        step_result = responder.continue_protocol_step(
            session_state,
            tool_results,
            system_prompt=self._responder_finalize_prompt(
                responder,
                reason=reason,
                question_limit=question_limit,
            ),
            tools=[],
            enable_web_search=False,
            round_number=round_number,
        )
        if self._stream_response_enabled:
            responder.emit_final_text(step_result.output_text or "")
        return step_result.output_text or ""

    def _run_regular_responder_protocol(self, responder, turn):
        search_rounds = 0
        model_round = 0
        try:
            responder.emit_state(
                ResponseState.DECIDE_NEXT_STEP,
                {
                    "round": 0,
                    "available_actions": ["answer_now", "ask_focused_follow_up_questions", "search"],
                },
            )
            step_result = responder.start_protocol_step(
                turn.user_question,
                turn.retrieved_docs,
                turn.summarized_conversation_history,
                turn.memory_snapshot_text,
                system_prompt=self._responder_decision_prompt(responder),
                tools=[self._build_responder_decision_tool()],
                enable_web_search=False,
                round_number=model_round,
            )
            while True:
                decision_call, decision_error = self._extract_expected_protocol_tool_call(
                    step_result,
                    self.RESPONDER_DECISION_TOOL_NAME,
                )
                if decision_call is None:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [],
                        reason=f"invalid_decision:{decision_error}",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                decision, decision_error = self._validate_responder_decision(decision_call)
                if decision is None:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [self._protocol_tool_result(decision_call, {"router_error": decision_error})],
                        reason=f"invalid_decision:{decision_error}",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                if decision["action"] == "search" and self._decision_prefers_follow_up_questions(decision):
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [
                            self._protocol_tool_result(
                                decision_call,
                                {
                                    "approved_action": "ask_focused_follow_up_questions",
                                    "router_reason": "environment_fact_gap_prefers_follow_up",
                                    "decision": decision,
                                },
                            )
                        ],
                        reason="environment_fact_gap_prefers_follow_up",
                        round_number=model_round + 1,
                        question_limit=3,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                if decision["action"] != "search":
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [
                            self._protocol_tool_result(
                                decision_call,
                                {
                                    "approved_action": decision["action"],
                                    "decision": decision,
                                },
                            )
                        ],
                        reason=decision["action"],
                        round_number=model_round + 1,
                        question_limit=3 if decision["action"] == "ask_focused_follow_up_questions" else None,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                if search_rounds >= self.response_tool_rounds:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [
                            self._protocol_tool_result(
                                decision_call,
                                {
                                    "approved_action": "finalize",
                                    "router_reason": "tool_budget_exhausted",
                                    "decision": decision,
                                },
                            )
                        ],
                        reason="tool_budget_exhausted",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                search_rounds += 1
                search_payload, retrieval_metadata = self._execute_regular_responder_search(decision)
                usefulness = retrieval_metadata.get("usefulness") or ""
                search_outcome = retrieval_metadata.get("search_outcome") or ""
                gate_action = retrieval_metadata.get("gate_action") or ""
                if gate_action in {GATE_REQUIRE_REASON, GATE_BLOCK}:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        step_result.session_state,
                        [self._protocol_tool_result(decision_call, search_payload)],
                        reason="search_justification_collapsed",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                model_round += 1
                responder.emit_state(
                    ResponseState.EVALUATE_TOOL_RESULT,
                    {
                        "round": search_rounds,
                        "search_outcome": search_outcome,
                        "usefulness": usefulness,
                    },
                )
                evaluation_step = responder.continue_protocol_step(
                    step_result.session_state,
                    [self._protocol_tool_result(decision_call, search_payload)],
                    system_prompt=self._responder_evaluation_prompt(responder),
                    tools=[self._build_responder_evaluation_tool()],
                    enable_web_search=False,
                    round_number=model_round,
                )
                evaluation_call, evaluation_error = self._extract_expected_protocol_tool_call(
                    evaluation_step,
                    self.RESPONDER_EVALUATION_TOOL_NAME,
                )
                if evaluation_call is None:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        evaluation_step.session_state,
                        [],
                        reason=f"invalid_evaluation:{evaluation_error}",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                evaluation, evaluation_error = self._validate_responder_evaluation(evaluation_call)
                if evaluation is None:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        evaluation_step.session_state,
                        [self._protocol_tool_result(evaluation_call, {"router_error": evaluation_error})],
                        reason=f"invalid_evaluation:{evaluation_error}",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                no_progress = (
                    usefulness == "zero"
                    or search_outcome in {"no_new_evidence", "search_exhausted_for_scope"}
                    or evaluation["progress_assessment"] == "no_meaningful_progress"
                )
                if no_progress:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        evaluation_step.session_state,
                        [self._protocol_tool_result(evaluation_call, {"accepted_evaluation": evaluation})],
                        reason="search_no_progress",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                if not evaluation["another_search_justified"]:
                    response_text = self._finalize_regular_responder(
                        responder,
                        turn,
                        evaluation_step.session_state,
                        [self._protocol_tool_result(evaluation_call, {"accepted_evaluation": evaluation})],
                        reason="evaluation_prefers_finalize",
                        round_number=model_round + 1,
                    )
                    responder.emit_state(ResponseState.COMPLETE, {"tool_rounds": search_rounds})
                    return response_text

                model_round += 1
                responder.emit_state(
                    ResponseState.DECIDE_NEXT_STEP,
                    {
                        "round": search_rounds,
                        "available_actions": ["answer_now", "ask_focused_follow_up_questions", "search"],
                        "prior_usefulness": usefulness,
                        "prior_search_outcome": search_outcome,
                    },
                )
                step_result = responder.continue_protocol_step(
                    evaluation_step.session_state,
                    [self._protocol_tool_result(evaluation_call, {"accepted_evaluation": evaluation})],
                    system_prompt=self._responder_decision_prompt(responder),
                    tools=[self._build_responder_decision_tool()],
                    enable_web_search=False,
                    round_number=model_round,
                )
        except Exception:
            responder.emit_state(ResponseState.ERROR, {})
            raise

    # Maps MagiState names to (role, phase) for caller metadata tagging
    _MAGI_STATE_TO_CALLER = {
        "ROLE_EAGER":            ("eager",    "opening"),
        "ROLE_SKEPTIC":          ("skeptic",  "opening"),
        "ROLE_HISTORIAN":        ("historian","opening"),
        "DISCUSSION_EAGER":      ("eager",    "discussion"),
        "DISCUSSION_SKEPTIC":    ("skeptic",  "discussion"),
        "DISCUSSION_HISTORIAN":  ("historian","discussion"),
        "CLOSING_EAGER":         ("eager",    "closing"),
        "CLOSING_SKEPTIC":       ("skeptic",  "closing"),
        "CLOSING_HISTORIAN":     ("historian","closing"),
    }

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
        # Update active caller metadata so retrieval tool calls can be tagged
        caller_pair = self._MAGI_STATE_TO_CALLER.get(state.name)
        if caller_pair:
            role, phase = caller_pair
            round_number = int((payload or {}).get("round") or 0)
            unresolved = str((payload or {}).get("unresolved_issue") or "")
            self._active_magi_caller = {
                "caller_role": role,
                "caller_phase": phase,
                "caller_round": round_number,
                "unresolved_issue": unresolved,
            }
        elif state.name in {"OPENING_ARGUMENTS", "DISCUSSION", "CLOSING_ARGUMENTS", "ARBITER", "COMPLETE"}:
            # Phase-level state; only update round and unresolved_issue if provided
            if payload:
                self._active_magi_caller.update({
                    "caller_round": int(payload.get("round") or self._active_magi_caller.get("caller_round", 0)),
                    "unresolved_issue": str(payload.get("unresolved_issue") or self._active_magi_caller.get("unresolved_issue", "")),
                })
        elif state.name in {"ERROR", "COMPLETE"}:
            self._active_magi_caller = {}

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
            historian_web_search_decider=self._should_enable_historian_web_search,
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
            historian_web_search_decider=self._should_enable_historian_web_search,
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
                    "Search the RAG database. Pass scope_hints to narrow by document metadata "
                    "before chunk-level search, or canonical_source_ids to pin specific documents. "
                    "Both are optional; relevant_documents remains the planner-supplied routing-domain hint."
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
                            "description": "Suggested routing-domain labels to bias or narrow the search",
                        },
                        "scope_hints": self._scope_hint_schema(),
                        "canonical_source_ids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": self._known_canonical_doc_ids(),
                            },
                            "description": (
                                "Optional. Pin the search to specific documents by canonical ID. "
                                "Use only when you know the exact document or documents to consult."
                            ),
                        },
                        "repeat_reason": {
                            "type": "string",
                            "enum": sorted(ALLOWED_REPEAT_REASONS),
                            "description": "Reason for repeating retrieval on the same scope after low-value or exhausted results",
                        },
                        "requested_evidence_goal": {
                            "type": "string",
                            "description": (
                                "Optional internal evidence goal such as install_component, "
                                "configure_access, verify_state, or troubleshoot_failure"
                            ),
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
        return MemoryExtractor(
            worker=self._build_worker(memory_extractor, self.settings.memory_extractor),
            event_listener=self._emit_event,
        )

    def _build_memory_store(self, memory_store):
        return memory_store

    def _active_evidence_pool(self) -> EvidencePool:
        turn = self.current_turn
        if turn is not None:
            if turn.evidence_pool is None:
                turn.evidence_pool = EvidencePool()
            return turn.evidence_pool
        return self._standalone_evidence_pool

    def _database_retrieve_context_result(
        self,
        query,
        searchable_labels,
        *,
        excluded_page_windows,
        excluded_block_keys,
        covered_region_keys,
        requested_evidence_goal,
        router_hint=None,
        explicit_doc_ids=(),
    ):
        if not hasattr(self.database, "retrieve_context_result"):
            return {
                "context_text": self.database.retrieve_context(query, searchable_labels),
                "selected_sources": [],
                "merged_blocks": [],
                "bundle_summaries": [],
                "retrieval_metadata": {},
            }

        retrieve_context_result = self.database.retrieve_context_result
        kwargs = {}
        try:
            signature = inspect.signature(retrieve_context_result)
            accepts_var_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if accepts_var_kwargs or "excluded_page_windows" in signature.parameters:
                kwargs["excluded_page_windows"] = excluded_page_windows
            if accepts_var_kwargs or "excluded_block_keys" in signature.parameters:
                kwargs["excluded_block_keys"] = excluded_block_keys
            if accepts_var_kwargs or "covered_region_keys" in signature.parameters:
                kwargs["covered_region_keys"] = covered_region_keys
            if accepts_var_kwargs or "requested_evidence_goal" in signature.parameters:
                kwargs["requested_evidence_goal"] = requested_evidence_goal
            if accepts_var_kwargs or "router_hint" in signature.parameters:
                kwargs["router_hint"] = router_hint
            if accepts_var_kwargs or "explicit_doc_ids" in signature.parameters:
                kwargs["explicit_doc_ids"] = explicit_doc_ids
        except (TypeError, ValueError):
            kwargs = {}
        return retrieve_context_result(query, searchable_labels, **kwargs) or {}

    def _retrieval_runtime_shape(self):
        config = getattr(self.database, "config", None)
        if config is None:
            return {}
        return {
            "db_path": getattr(config, "db_path", None),
            "table_name": getattr(config, "table_name", None),
            "embed_provider_name": getattr(config, "embed_provider_name", None),
            "embed_model_name": getattr(config, "embed_model_name", None),
            "rerank_provider_name": getattr(config, "rerank_provider_name", None),
            "rerank_model_name": getattr(config, "rerank_model_name", None),
            "initial_fetch": getattr(config, "initial_fetch", None),
            "final_top_k": getattr(config, "final_top_k", None),
            "neighbor_pages": getattr(config, "neighbor_pages", None),
            "max_expanded": getattr(config, "max_expanded", None),
            "source_profile_sample": getattr(config, "source_profile_sample", None),
        }

    def _retrieval_fingerprint(
        self,
        query,
        searchable_labels,
        excluded_page_windows,
        excluded_block_keys,
        *,
        requested_evidence_goal="",
        router_hint=None,
        explicit_doc_ids=(),
    ):
        payload = {
            "query": query,
            "searchable_labels": list(searchable_labels or []),
            "requested_evidence_goal": requested_evidence_goal or "",
            "router_hint": router_hint or {},
            "explicit_doc_ids": sorted(explicit_doc_ids or ()),
            "excluded_page_windows": [
                {
                    "key": window.get("key"),
                    "source": window.get("source"),
                    "page_start": window.get("page_start"),
                    "page_end": window.get("page_end"),
                }
                for window in excluded_page_windows
            ],
            "excluded_block_keys": list(excluded_block_keys or []),
            "runtime_shape": self._retrieval_runtime_shape(),
        }
        return json.dumps(payload, sort_keys=True)

    def _active_caller_metadata(self) -> dict:
        """Return the current MAGI caller context (role/phase/round/unresolved_issue)."""
        if self._magi_active in {"full", "lite"}:
            return dict(self._active_magi_caller)
        return {}

    def _derive_requested_evidence_goal(self, query: str, *, repeat_reason: str = "", unresolved_issue: str = "") -> str:
        canonical_repeat_reason = normalize_repeat_reason(repeat_reason)
        if canonical_repeat_reason == "contradiction_check":
            return "confirm_contradiction"
        if canonical_repeat_reason == "alternate_source_confirmation":
            return "gather_alternate_source"
        if canonical_repeat_reason == "expand_beyond_covered_region":
            return "expand_covered_region"
        if canonical_repeat_reason == "fill_named_unresolved_gap":
            return "fill_unresolved_gap"

        lowered = f"{(query or '').lower()} {(unresolved_issue or '').lower()}".strip()
        if not lowered:
            return ""
        if any(token in lowered for token in ("prereq", "prerequisite", "requirement", "before ")):
            return "identify_prerequisites"
        if any(token in lowered for token in ("create ", "provision", "deploy", "build ", "bring up", "spin up")):
            return "create_target"
        if any(token in lowered for token in ("configure", "set up", "setup", "enable", "expose", "connect", "reachability", "forward")):
            return "configure_access"
        if any(token in lowered for token in ("install", "add package", "add component", "download and install")):
            return "install_component"
        if any(token in lowered for token in ("verify", "validate", "confirm", "check whether", "check if")):
            return "verify_state"
        if any(token in lowered for token in ("error", "fail", "failure", "broken", "not working", "issue", "troubleshoot", "debug", "why ")):
            return "troubleshoot_failure"
        return ""

    def _should_enable_historian_web_search(self, *, phase: str = "", round_number: int = 0, unresolved_issue: str = "") -> bool:
        del phase, round_number, unresolved_issue
        return self._active_evidence_pool().historian_web_fallback_allowed()

    def _gate_message(self, gate, *, requested_evidence_goal: str = "", caller_role: str = "") -> str:
        goal_note = requested_evidence_goal or "a clearer requested_evidence_goal"
        if gate.action == GATE_REQUIRE_REASON:
            if caller_role:
                return (
                    "Evidence pool requires an explicit repeat_reason for this MAGI scope before another retrieval. "
                    f"Refine the evidence goal or supply one of the allowed reasons. Current goal: {goal_note}."
                )
            return (
                "Evidence pool suggests refining the retrieval before repeating this scope. "
                f"Try a narrower requested_evidence_goal or provide a repeat_reason. Current goal: {goal_note}."
            )
        if gate.action == GATE_BLOCK:
            return (
                "Evidence pool blocked another retrieval on this scope because it is hard exhausted. "
                f"Refine the goal or change scope before retrying. Current goal: {goal_note}."
            )
        return ""

    def _annotate_retrieval_result(self, retrieval_result, query_record, gate):
        retrieval_metadata = dict(retrieval_result.get("retrieval_metadata") or {})
        retrieval_metadata.update(
            {
                "gate_action": gate.action,
                "scope_key": query_record.scope_key,
                "usefulness": query_record.usefulness,
                "usefulness_reason": query_record.usefulness_reason,
                "search_outcome": query_record.outcome,
                "requested_evidence_goal": query_record.requested_evidence_goal,
                "gap_type": query_record.gap_type,
                "repeat_reason": query_record.repeat_reason,
                "soft_exhausted_scope_keys": sorted(self._active_evidence_pool().scope_state.soft_exhausted_scope_keys),
                "hard_exhausted_scope_keys": sorted(self._active_evidence_pool().scope_state.hard_exhausted_scope_keys),
            }
        )
        retrieval_result["retrieval_metadata"] = retrieval_metadata
        return retrieval_result

    def _retrieve_with_pool(
        self,
        query,
        searchable_labels,
        *,
        repeat_reason: str = "",
        requested_evidence_goal: str = "",
        unresolved_issue: str = "",
        gap_type: str = "",
        strict_repeat_reason: bool = False,
        router_hint=None,
        explicit_doc_ids=(),
    ):
        """Core retrieval entry point. Uses the EvidencePool for gating, caching, and outcome tracking."""
        pool = self._active_evidence_pool()
        caller = self._active_caller_metadata()
        unresolved_issue = unresolved_issue or caller.get("unresolved_issue", "")
        gap_type = normalize_gap_type(gap_type)
        requested_evidence_goal = normalize_evidence_goal(requested_evidence_goal) or self._derive_requested_evidence_goal(
            query,
            repeat_reason=repeat_reason,
            unresolved_issue=unresolved_issue,
        )

        # --- Gate check ---
        gate = pool.check_gate(
            query,
            searchable_labels,
            caller_role=caller.get("caller_role", ""),
            repeat_reason=repeat_reason,
            requested_evidence_goal=requested_evidence_goal,
            unresolved_issue=unresolved_issue,
            gap_type=gap_type,
            strict_repeat_reason=strict_repeat_reason,
        )
        if not gate.allow_search:
            gated_message = self._gate_message(
                gate,
                requested_evidence_goal=requested_evidence_goal,
                caller_role=caller.get("caller_role", ""),
            )
            self._emit_event(
                "retrieval_gated",
                {
                    "query": query,
                    "gate_action": gate.action,
                    "scope_key": gate.scope_key,
                    "scope_exhausted": gate.scope_exhausted,
                    "exhaustion_level": gate.exhaustion_level,
                    "blocked_reason": gate.blocked_reason,
                    "requested_evidence_goal": requested_evidence_goal,
                    **caller,
                },
            )
            empty_result = {
                "context_text": gated_message,
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
                    "cached_hit": False,
                    "gated": True,
                    "gated_reason": gate.blocked_reason,
                    "gate_action": gate.action,
                    "gated_message": gated_message,
                    "scope_key": gate.scope_key,
                    "requested_evidence_goal": requested_evidence_goal,
                },
            }
            return empty_result, False

        # --- Build exclusion inputs from pool coverage ---
        excluded_page_windows = pool.coverage_as_excluded_page_windows()
        excluded_block_keys = pool.coverage_as_excluded_block_keys()

        # --- Exact fingerprint cache check ---
        fingerprint = self._retrieval_fingerprint(
            query,
            searchable_labels,
            excluded_page_windows,
            excluded_block_keys,
            requested_evidence_goal=requested_evidence_goal,
            router_hint=router_hint,
            explicit_doc_ids=explicit_doc_ids,
        )
        cached_result = pool._cache.get(fingerprint)

        # --- Record query before retrieval ---
        q_record = pool.record_query(
            raw_query=query,
            searchable_labels=list(searchable_labels or []),
            caller_role=caller.get("caller_role", ""),
            caller_phase=caller.get("caller_phase", ""),
            caller_round=int(caller.get("caller_round") or 0),
            unresolved_issue=unresolved_issue,
            gap_type=gap_type,
            requested_evidence_goal=requested_evidence_goal,
            repeat_reason=repeat_reason,
        )

        if cached_result is not None:
            retrieval_result = deepcopy(cached_result)
            retrieval_metadata = dict(retrieval_result.get("retrieval_metadata") or {})
            retrieval_metadata["cached_hit"] = True
            retrieval_result["retrieval_metadata"] = retrieval_metadata
            pool.record_evidence_from_result(retrieval_result, q_record, is_cache_hit=True)
            self._annotate_retrieval_result(retrieval_result, q_record, gate)
            self._emit_event(
                "evidence_pool_update",
                {**pool.summary_event_payload(), "query": query, **caller},
            )
            return retrieval_result, True

        # --- Fresh retrieval ---
        retrieval_result = self._database_retrieve_context_result(
            query,
            searchable_labels,
            excluded_page_windows=excluded_page_windows,
            excluded_block_keys=excluded_block_keys,
            covered_region_keys=pool.known_covered_region_keys(),
            requested_evidence_goal=requested_evidence_goal,
            router_hint=router_hint,
            explicit_doc_ids=explicit_doc_ids,
        )
        retrieval_metadata = dict(retrieval_result.get("retrieval_metadata") or {})
        retrieval_metadata["cached_hit"] = False
        retrieval_result["retrieval_metadata"] = retrieval_metadata
        pool._cache[fingerprint] = deepcopy(retrieval_result)
        pool.record_evidence_from_result(retrieval_result, q_record, is_cache_hit=False)
        self._annotate_retrieval_result(retrieval_result, q_record, gate)
        self._emit_event(
            "evidence_pool_update",
            {**pool.summary_event_payload(), "query": query, **caller},
        )
        return retrieval_result, False

    def _retrieve_with_ledger(self, query, searchable_labels, **kwargs):
        """Public-facing retrieval entry point (name kept for compatibility)."""
        return self._retrieve_with_pool(query, searchable_labels, **kwargs)

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
                    tool_complete_payload = None
                else:
                    searchable_labels = self._searchable_labels(relevant_documents)
                    scope_hints = self._validated_scope_hints(tool_args.get("scope_hints"))
                    explicit_doc_ids = self._validated_canonical_doc_ids(tool_args.get("canonical_source_ids"))
                    repeat_reason = normalize_repeat_reason(tool_args.get("repeat_reason", ""))
                    requested_evidence_goal = normalize_evidence_goal(tool_args.get("requested_evidence_goal", "")) or self._derive_requested_evidence_goal(
                        query,
                        repeat_reason=repeat_reason,
                        unresolved_issue=self._active_caller_metadata().get("unresolved_issue", ""),
                    )
                    retrieval_result, cached_hit = self._retrieve_with_ledger(
                        query,
                        searchable_labels,
                        repeat_reason=repeat_reason,
                        requested_evidence_goal=requested_evidence_goal,
                        unresolved_issue=self._active_caller_metadata().get("unresolved_issue", ""),
                        router_hint=scope_hints,
                        explicit_doc_ids=explicit_doc_ids,
                    )
                    retrieval_metadata = retrieval_result.get("retrieval_metadata") or {}
                    result = str(retrieval_result.get("context_text") or "")
                    pool = self._active_evidence_pool()
                    caller = self._active_caller_metadata()
                    tool_complete_payload = {
                        "name": tool_name,
                        "result_size": len(result),
                        "result_text": result,
                        "result_blocks": list(retrieval_result.get("merged_blocks") or []),
                        "selected_sources": list(retrieval_result.get("selected_sources") or []),
                        "cached": cached_hit,
                        "anchor_count": retrieval_metadata.get("anchor_count", 0),
                        "anchor_pages": list(retrieval_metadata.get("anchor_pages") or []),
                        "fetched_neighbor_pages": list(retrieval_metadata.get("fetched_neighbor_pages") or []),
                        "delivered_bundle_count": retrieval_metadata.get("delivered_bundle_count", 0),
                        "excluded_seen_count": retrieval_metadata.get("excluded_seen_count", 0),
                        "skipped_bundle_count": retrieval_metadata.get("skipped_bundle_count", 0),
                        # Evidence pool fields
                        "gate_action": retrieval_metadata.get("gate_action", ""),
                        "search_outcome": retrieval_metadata.get("search_outcome") or pool.last_query_outcome(),
                        "usefulness": retrieval_metadata.get("usefulness") or pool.last_query_usefulness(),
                        "usefulness_reason": retrieval_metadata.get("usefulness_reason", ""),
                        "scope_key": retrieval_metadata.get("scope_key") or pool.last_query_scope_key(),
                        "covered_region_count": len(pool.known_covered_region_keys()),
                        "scope_exhausted": bool(pool.scope_state.exhausted_scope_keys),
                        "soft_exhausted_scope_keys": sorted(pool.scope_state.soft_exhausted_scope_keys),
                        "hard_exhausted_scope_keys": sorted(pool.scope_state.hard_exhausted_scope_keys),
                        "requested_evidence_goal": retrieval_metadata.get("requested_evidence_goal", requested_evidence_goal),
                        "gap_type": retrieval_metadata.get("gap_type", ""),
                        "repeat_reason": retrieval_metadata.get("repeat_reason", repeat_reason),
                        "caller_role": caller.get("caller_role", ""),
                        "caller_phase": caller.get("caller_phase", ""),
                        "caller_round": caller.get("caller_round", 0),
                    }
            elif tool_name == "search_conversation_history":
                self._append_trace_marker("TOOL_SEARCH_HISTORY")
                result = self._search_conversation_history(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
                tool_complete_payload = None
            elif tool_name == "get_system_profile":
                result = self.memory_store.format_system_profile()
                tool_complete_payload = None
            elif tool_name == "search_memory_issues":
                result = self.memory_store.search_issues(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
                tool_complete_payload = None
            elif tool_name == "search_attempt_log":
                result = self.memory_store.search_attempts(
                    tool_args.get("query", ""),
                    tool_args.get("max_results", 5),
                )
                tool_complete_payload = None
            else:
                result = {"error": f"unknown tool '{tool_name}'"}
                tool_complete_payload = None
        except Exception as exc:
            result = {"error": str(exc)}
            tool_complete_payload = None

        result_size = len(result) if isinstance(result, str) else len(str(result))
        if isinstance(result, dict) and "error" in result:
            self._emit_event(
                "tool_error",
                {"name": tool_name, "error": result["error"]},
            )
        else:
            self._emit_event(
                "tool_complete",
                tool_complete_payload or {"name": tool_name, "result_size": result_size},
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
            turn.retrieved_context_blocks = []
            return RouterState.GENERATE_RESPONSE
        return RouterState.REWRITE_QUERY

    def _retrieve_context(self, turn):
        requested_evidence_goal = self._derive_requested_evidence_goal(
            turn.retrieval_query or turn.user_question,
        )
        retrieval_result, _ = self._retrieve_with_ledger(
            turn.retrieval_query,
            turn.suggested_search_labels,
            requested_evidence_goal=requested_evidence_goal,
        )
        retrieval_metadata = retrieval_result.get("retrieval_metadata") or {}
        if retrieval_metadata.get("gated"):
            turn.retrieved_docs = ""
            turn.retrieved_context_blocks = []
        else:
            turn.retrieved_docs = str(retrieval_result.get("context_text") or "")
            turn.retrieved_context_blocks = list(retrieval_result.get("merged_blocks") or [])
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

        # Build evidence pool summary for MAGI modes
        evidence_pool_summary = ""
        if self._magi_active in {"full", "lite"}:
            pool = self._active_evidence_pool()
            evidence_pool_summary = pool.build_prompt_summary()

        if turn.magi_resume_state and hasattr(responder, "resume_api"):
            turn.response = responder.resume_api(
                turn.user_question,
                turn.retrieved_docs,
                turn.summarized_conversation_history,
                turn.memory_snapshot_text,
                pause_state=turn.magi_resume_state,
                stream=self._stream_response_enabled,
                evidence_pool_summary=evidence_pool_summary,
            )
        else:
            if self._regular_responder_supports_router_protocol(responder):
                turn.response = self._run_regular_responder_protocol(responder, turn)
            else:
                responder_method = responder.stream_api if self._stream_response_enabled else responder.call_api
                kwargs = {}
                if evidence_pool_summary:
                    kwargs["evidence_pool_summary"] = evidence_pool_summary
                turn.response = responder_method(
                    turn.user_question,
                    turn.retrieved_docs,
                    turn.summarized_conversation_history,
                    turn.memory_snapshot_text,
                    **kwargs,
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
                    "items": extracted,
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
            self._emit_event(
                "memory_resolved",
                {
                    **turn.memory_resolution.details(),
                    "committed_full": turn.memory_resolution.committed,
                    "candidates_full": list(turn.memory_resolution.candidates),
                    "conflicts_full": list(turn.memory_resolution.conflicts),
                    "session_summary": turn.memory_resolution.session_summary,
                },
            )
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
            self._emit_event(
                "memory_committed",
                {
                    **turn.memory_resolution.details(),
                    "committed_full": turn.memory_resolution.committed,
                    "candidates_full": list(turn.memory_resolution.candidates),
                    "conflicts_full": list(turn.memory_resolution.conflicts),
                    "session_summary": turn.memory_resolution.session_summary,
                },
            )
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
