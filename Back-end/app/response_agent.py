from enum import Enum, auto

from history_preparer import PreparedHistory
from openAI_caller import OpenAIWorker
from prompts import CHATBOT_SYSTEM_PROMPT


class ResponseState(Enum):
    PREPARE_REQUEST = auto()
    REQUEST_MODEL = auto()
    PROCESS_TOOL_CALLS = auto()
    SUBMIT_TOOL_RESULTS = auto()
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
        state_listener=None,
    ):
        self.worker = worker or OpenAIWorker()
        self.chatbot_prompt = chatbot_prompt
        self.tools = tools or []
        self.tool_handler = tool_handler
        self.max_tool_rounds = max_tool_rounds
        self.state_listener = state_listener

    def _set_state(self, state, payload=None):
        if self.state_listener is not None:
            self.state_listener(state, payload or {})

    def _handle_worker_event(self, event_type, payload):
        if event_type == "request_submitted":
            self._set_state(ResponseState.REQUEST_MODEL, payload)
        elif event_type == "tool_calls_received":
            self._set_state(ResponseState.PROCESS_TOOL_CALLS, payload)
        elif event_type == "tool_results_submitted":
            self._set_state(ResponseState.SUBMIT_TOOL_RESULTS, payload)
        elif event_type == "response_completed":
            self._set_state(ResponseState.COMPLETE, payload)

    def call_api(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        current_turn_content = f"""
        PRIOR CONVERSATION SUMMARY:
        {history_summary_text}

        KNOWN SYSTEM MEMORY:
        {memory_snapshot_text}

        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {retrieved_docs}

        USER QUESTION:
        {user_query}
        """
        self._set_state(ResponseState.PREPARE_REQUEST, {})
        try:
            response = self.worker.generate_text(
                system_prompt=self.chatbot_prompt,
                user_message=current_turn_content,
                history=summarized_conversation_history.recent_turns,
                tools=self.tools,
                tool_handler=self.tool_handler,
                max_tool_rounds=self.max_tool_rounds,
                event_listener=self._handle_worker_event,
            )
        except Exception:
            self._set_state(ResponseState.ERROR, {})
            raise

        return response
