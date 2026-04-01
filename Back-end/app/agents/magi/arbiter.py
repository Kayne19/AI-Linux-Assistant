from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import call_with_optional_cancel_check, invoke_cancel_check
from prompting.prompts import CHATBOT_SYSTEM_PROMPT
from prompting.magi_prompts import MAGI_ARBITER_PROMPT


class MagiArbiter:
    def __init__(self, worker, tools=None, tool_handler=None, max_tool_rounds=4, event_listener=None, cancel_check=None):
        self.worker = worker
        self.tools = tools or []
        self.tool_handler = tool_handler
        self.max_tool_rounds = max_tool_rounds
        self.event_listener = event_listener
        self.system_prompt = CHATBOT_SYSTEM_PROMPT + "\n\n" + MAGI_ARBITER_PROMPT
        self.cancel_check = cancel_check

    def _emit_event(self, event_type, payload):
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _forward_worker_event(self, event_type, payload):
        if event_type == "text_delta":
            return
        self._emit_event(event_type, payload)

    def synthesize(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, deliberation_transcript):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()

        user_message = f"""
PRIOR CONVERSATION SUMMARY:
{history_summary_text}

KNOWN SYSTEM MEMORY:
{memory_snapshot_text}

REFERENCE CONTEXT:
{retrieved_docs}

DELIBERATION TRANSCRIPT:
{deliberation_transcript}

USER QUESTION:
{user_query}
""".strip()

        invoke_cancel_check(self.cancel_check, "before_model_call:arbiter")
        response = call_with_optional_cancel_check(
            self.worker.generate_text,
            cancel_check=self.cancel_check,
            system_prompt=self.system_prompt,
            user_message=user_message,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=self._forward_worker_event,
        )
        invoke_cancel_check(self.cancel_check, "after_model_call:arbiter")
        return response

    def synthesize_stream(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, deliberation_transcript):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()

        user_message = f"""
PRIOR CONVERSATION SUMMARY:
{history_summary_text}

KNOWN SYSTEM MEMORY:
{memory_snapshot_text}

REFERENCE CONTEXT:
{retrieved_docs}

DELIBERATION TRANSCRIPT:
{deliberation_transcript}

USER QUESTION:
{user_query}
""".strip()

        stream_method = getattr(self.worker, "generate_text_stream", None)
        if callable(stream_method):
            invoke_cancel_check(self.cancel_check, "before_model_call:arbiter")
            response = call_with_optional_cancel_check(
                stream_method,
                cancel_check=self.cancel_check,
                system_prompt=self.system_prompt,
                user_message=user_message,
                history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
                tools=self.tools,
                tool_handler=self.tool_handler,
                max_tool_rounds=self.max_tool_rounds,
                event_listener=self._forward_worker_event,
            )
            invoke_cancel_check(self.cancel_check, "after_model_call:arbiter")
            if response:
                self._emit_event("text_delta", {"delta": response})
            return response
        return self.synthesize(
            user_query, retrieved_docs, summarized_conversation_history,
            memory_snapshot_text, deliberation_transcript,
        )
