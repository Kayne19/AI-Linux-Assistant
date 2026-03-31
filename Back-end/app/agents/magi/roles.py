import json

from orchestration.history_preparer import PreparedHistory
from prompting.magi_prompts import (
    MAGI_CLOSING_PROMPT_TEMPLATE,
    MAGI_DISCUSSION_PROMPT_TEMPLATE,
    MAGI_EAGER_SYSTEM_PROMPT,
    MAGI_HISTORIAN_SYSTEM_PROMPT,
    MAGI_SKEPTIC_SYSTEM_PROMPT,
    ROLE_REMINDERS,
)


class MagiRole:
    role_name = "role"
    system_prompt = ""

    def __init__(self, worker, tools=None, tool_handler=None, max_tool_rounds=4, event_listener=None):
        self.worker = worker
        self.tools = tools or []
        self.tool_handler = tool_handler
        self.max_tool_rounds = max_tool_rounds
        self.event_listener = event_listener

    def _emit_event(self, event_type, payload):
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _build_context_bundle(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        return f"""
PRIOR CONVERSATION SUMMARY:
{history_summary_text}

KNOWN SYSTEM MEMORY:
{memory_snapshot_text}

REFERENCE CONTEXT:
{retrieved_docs}

USER QUESTION:
{user_query}
""".strip()

    def _build_opening_message(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text):
        return self._build_context_bundle(
            user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
        )

    def _parse_response(self, raw_text):
        raw_text = (raw_text or "").strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines).strip()
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "position" in parsed:
                parsed.setdefault("confidence", "medium")
                parsed.setdefault("key_claims", [])
                parsed.setdefault("best_next_check", "")
                parsed.setdefault("missing_evidence", [])
                parsed.setdefault("evidence_sources", [])
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "position": raw_text,
            "confidence": "medium",
            "key_claims": [],
            "best_next_check": "",
            "missing_evidence": [],
            "evidence_sources": [],
        }

    def _get_generate_fn(self, event_listener):
        """Use generate_text_stream when a custom listener is provided and the worker supports it."""
        if event_listener is not None:
            stream_fn = getattr(self.worker, "generate_text_stream", None)
            if callable(stream_fn):
                return stream_fn
        return self.worker.generate_text

    def opening_argument(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", event_listener=None):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        user_message = self._build_opening_message(
            user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
        )
        raw = self._get_generate_fn(event_listener)(
            system_prompt=self.system_prompt,
            user_message=user_message,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=listener,
        )
        return self._parse_response(raw)

    def discuss(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", transcript="", round_number=1, event_listener=None):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        discussion_prompt = MAGI_DISCUSSION_PROMPT_TEMPLATE.format(
            role_name=self.role_name.upper(),
            round_number=round_number,
            user_query=user_query,
            history_summary_text=history_summary_text,
            memory_snapshot_text=memory_snapshot_text,
            retrieved_docs=retrieved_docs,
            transcript=transcript,
            role_reminder=ROLE_REMINDERS.get(self.role_name, ""),
        )
        raw = self._get_generate_fn(event_listener)(
            system_prompt=self.system_prompt,
            user_message=discussion_prompt,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=listener,
        )
        parsed = self._parse_response(raw)
        if "new_information" not in parsed:
            parsed["new_information"] = bool(parsed.get("position", "").strip())
        return parsed

    def closing_argument(self, user_query, transcript, event_listener=None):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        closing_prompt = MAGI_CLOSING_PROMPT_TEMPLATE.format(
            role_name=self.role_name.upper(),
            role_reminder=ROLE_REMINDERS.get(self.role_name, ""),
            user_query=user_query,
            transcript=transcript,
        )
        raw = self._get_generate_fn(event_listener)(
            system_prompt=self.system_prompt,
            user_message=closing_prompt,
            history=[],
            tools=[],
            tool_handler=None,
            max_tool_rounds=0,
            event_listener=listener,
        )
        return self._parse_response(raw)

    def _forward_worker_event(self, event_type, payload):
        self._emit_event(event_type, payload)


class MagiEager(MagiRole):
    role_name = "eager"
    system_prompt = MAGI_EAGER_SYSTEM_PROMPT


class MagiSkeptic(MagiRole):
    role_name = "skeptic"
    system_prompt = MAGI_SKEPTIC_SYSTEM_PROMPT


class MagiHistorian(MagiRole):
    role_name = "historian"
    system_prompt = MAGI_HISTORIAN_SYSTEM_PROMPT
