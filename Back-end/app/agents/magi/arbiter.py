import json

from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import call_with_optional_cancel_check, invoke_cancel_check
from prompting.prompts import CHATBOT_SYSTEM_PROMPT
from prompting.magi_prompts import MAGI_ARBITER_PROMPT

VALID_DECISION_MODES = {"consensus", "best_current_branch"}
VALID_UNCERTAINTY_LEVELS = {"high", "medium", "low"}


def _clean_text(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()


def _clean_list(value):
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _clean_text(item)
        if text:
            cleaned.append(text)
    return cleaned


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

    def _parse_response(self, raw_text):
        raw_text = _clean_text(raw_text)
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            raw_text = "\n".join(lines).strip()
        parsed = None
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if not isinstance(parsed, dict):
            parsed = {"final_answer": raw_text}

        decision_mode = _clean_text(parsed.get("decision_mode")).lower()
        if decision_mode not in VALID_DECISION_MODES:
            decision_mode = "best_current_branch"

        uncertainty_level = _clean_text(parsed.get("uncertainty_level")).lower()
        if uncertainty_level not in VALID_UNCERTAINTY_LEVELS:
            uncertainty_level = "medium"

        final_answer = _clean_text(parsed.get("final_answer"))
        if not final_answer:
            final_answer = raw_text

        return {
            "decision_mode": decision_mode,
            "uncertainty_level": uncertainty_level,
            "winning_branch": _clean_text(parsed.get("winning_branch")),
            "strongest_surviving_objection": _clean_text(parsed.get("strongest_surviving_objection")),
            "missing_decisive_artifact": _clean_text(parsed.get("missing_decisive_artifact")),
            "evidence_sources": _clean_list(parsed.get("evidence_sources")),
            "final_answer": final_answer,
        }

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
        return self._parse_response(response)

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
            parsed = self._parse_response(response)
            if parsed["final_answer"]:
                self._emit_event("text_delta", {"delta": parsed["final_answer"]})
            return parsed
        return self.synthesize(
            user_query, retrieved_docs, summarized_conversation_history,
            memory_snapshot_text, deliberation_transcript,
        )
