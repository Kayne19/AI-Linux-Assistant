from enum import Enum, auto

from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import call_with_optional_cancel_check, invoke_cancel_check
from providers.openAI_caller import OpenAIWorker
from prompting.prompts import CHATBOT_SYSTEM_PROMPT


class ResponseState(Enum):
    PREPARE_REQUEST = auto()
    REQUEST_MODEL = auto()
    WEB_SEARCH = auto()
    PROCESS_TOOL_CALLS = auto()
    EVALUATE_TOOL_RESULT = auto()
    SUBMIT_TOOL_RESULTS = auto()
    FINALIZE_RESPONSE = auto()
    COMPLETE = auto()
    ERROR = auto()


class ResponseAgent:
    def __init__(
        self,
        worker=None,
        chatbot_prompt=CHATBOT_SYSTEM_PROMPT,
        tools=None,
        tool_handler=None,
        max_tool_rounds=8,
        enable_native_web_search=True,
        state_listener=None,
        event_listener=None,
        cancel_check=None,
    ):
        self.worker = worker or OpenAIWorker()
        self.chatbot_prompt = chatbot_prompt
        self.tools = tools or []
        self.tool_handler = tool_handler
        self.max_tool_rounds = max_tool_rounds
        self.enable_native_web_search = enable_native_web_search
        self.state_listener = state_listener
        self.event_listener = event_listener
        self.cancel_check = cancel_check

    def _set_state(self, state, payload=None):
        if self.state_listener is not None:
            self.state_listener(state, payload or {})

    def _handle_worker_event(self, event_type, payload):
        if event_type == "request_submitted":
            self._set_state(ResponseState.REQUEST_MODEL, payload)
        elif event_type == "web_search_used":
            self._set_state(ResponseState.WEB_SEARCH, payload)
        elif event_type == "tool_calls_received":
            self._set_state(ResponseState.PROCESS_TOOL_CALLS, payload)
        elif event_type == "tool_results_submitted":
            self._set_state(ResponseState.SUBMIT_TOOL_RESULTS, payload)
        elif event_type == "response_completed":
            self._set_state(ResponseState.COMPLETE, payload)
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _handle_stream_worker_event(self, event_type, payload):
        if event_type == "text_delta":
            return
        self._handle_worker_event(event_type, payload)

    def emit_state(self, state, payload=None):
        self._set_state(state, payload)

    def emit_final_text(self, response_text):
        if response_text and self.event_listener is not None:
            self.event_listener("text_delta", {"delta": response_text})

    def supports_router_protocol(self):
        return callable(getattr(self.worker, "start_text_step", None)) and callable(
            getattr(self.worker, "continue_text_step", None)
        )

    def _build_turn_content(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        return f"""
        PRIOR CONVERSATION SUMMARY:
        {history_summary_text}

        KNOWN SYSTEM MEMORY:
        {memory_snapshot_text}

        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {retrieved_docs}

        USER QUESTION:
        {user_query}
        """

    def start_protocol_step(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
        *,
        tools=None,
        enable_web_search=None,
        round_number=0,
    ):
        current_turn_content = self._build_turn_content(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
        )
        self._set_state(ResponseState.PREPARE_REQUEST, {})
        invoke_cancel_check(self.cancel_check, "before_model_call")
        step_result = call_with_optional_cancel_check(
            self.worker.start_text_step,
            cancel_check=self.cancel_check,
            system_prompt=self.chatbot_prompt,
            user_message=current_turn_content,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools if tools is None else tools,
            enable_web_search=self.enable_native_web_search if enable_web_search is None else enable_web_search,
            event_listener=self._handle_worker_event,
            cache_config={
                "enabled": True,
                "scope": "chat_responder",
            },
            round_number=round_number,
        )
        invoke_cancel_check(self.cancel_check, "after_model_call")
        return step_result

    def continue_protocol_step(
        self,
        session_state,
        tool_results,
        *,
        tools=None,
        enable_web_search=None,
        round_number=1,
    ):
        invoke_cancel_check(self.cancel_check, "before_model_call")
        step_result = call_with_optional_cancel_check(
            self.worker.continue_text_step,
            cancel_check=self.cancel_check,
            system_prompt=self.chatbot_prompt,
            session_state=session_state,
            tool_results=tool_results,
            tools=self.tools if tools is None else tools,
            enable_web_search=self.enable_native_web_search if enable_web_search is None else enable_web_search,
            event_listener=self._handle_worker_event,
            cache_config={
                "enabled": True,
                "scope": "chat_responder",
            },
            round_number=round_number,
        )
        invoke_cancel_check(self.cancel_check, "after_model_call")
        return step_result

    def call_api(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        current_turn_content = self._build_turn_content(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
        )
        self._set_state(ResponseState.PREPARE_REQUEST, {})
        try:
            invoke_cancel_check(self.cancel_check, "before_model_call")
            response = call_with_optional_cancel_check(
                self.worker.generate_text,
                cancel_check=self.cancel_check,
                system_prompt=self.chatbot_prompt,
                user_message=current_turn_content,
                history=summarized_conversation_history.recent_turns,
                tools=self.tools,
                tool_handler=self.tool_handler,
                max_tool_rounds=self.max_tool_rounds,
                enable_web_search=self.enable_native_web_search,
                event_listener=self._handle_worker_event,
                cache_config={
                    "enabled": True,
                    "scope": "chat_responder",
                },
            )
            invoke_cancel_check(self.cancel_check, "after_model_call")
        except Exception:
            self._set_state(ResponseState.ERROR, {})
            raise

        return response

    def stream_api(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        current_turn_content = self._build_turn_content(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
        )
        self._set_state(ResponseState.PREPARE_REQUEST, {})
        try:
            stream_method = getattr(self.worker, "generate_text_stream", None)
            if callable(stream_method):
                invoke_cancel_check(self.cancel_check, "before_model_call")
                response = call_with_optional_cancel_check(
                    stream_method,
                    cancel_check=self.cancel_check,
                    system_prompt=self.chatbot_prompt,
                    user_message=current_turn_content,
                    history=summarized_conversation_history.recent_turns,
                    tools=self.tools,
                    tool_handler=self.tool_handler,
                    max_tool_rounds=self.max_tool_rounds,
                    enable_web_search=self.enable_native_web_search,
                    event_listener=self._handle_stream_worker_event,
                    cache_config={
                        "enabled": True,
                        "scope": "chat_responder",
                    },
                )
                invoke_cancel_check(self.cancel_check, "after_model_call")
                if response and self.event_listener is not None:
                    self.event_listener("text_delta", {"delta": response})
            else:
                response = self.call_api(
                    user_query,
                    retrieved_docs,
                    summarized_conversation_history,
                    memory_snapshot_text,
                )
        except Exception:
            self._set_state(ResponseState.ERROR, {})
            raise

        return response
