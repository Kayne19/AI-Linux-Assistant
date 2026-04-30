import json
import re

from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import (
    call_with_optional_cancel_check,
    invoke_cancel_check,
)
from prompting.prompts import CHATBOT_SYSTEM_PROMPT
from prompting.magi_prompts import MAGI_ARBITER_PROMPT

VALID_DECISION_MODES = {"consensus", "best_current_branch"}
VALID_UNCERTAINTY_LEVELS = {"high", "medium", "low"}

MAGI_ARBITER_OUTPUT_SCHEMA = {
    "title": "magi_arbiter_output",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "primary_issue": {"type": "string"},
        "immediate_obligation": {"type": "string"},
        "winning_branch": {"type": "string"},
        "decision_mode": {"type": "string", "enum": sorted(VALID_DECISION_MODES)},
        "uncertainty_level": {
            "type": "string",
            "enum": sorted(VALID_UNCERTAINTY_LEVELS),
        },
        "strongest_surviving_objection": {"type": "string"},
        "missing_decisive_artifact": {"type": "string"},
        "evidence_sources": {"type": "array", "items": {"type": "string"}},
        "final_answer": {"type": "string"},
    },
    "required": [
        "primary_issue",
        "immediate_obligation",
        "winning_branch",
        "decision_mode",
        "uncertainty_level",
        "strongest_surviving_objection",
        "missing_decisive_artifact",
        "evidence_sources",
        "final_answer",
    ],
}


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


def _first_sentence(text):
    text = _clean_text(text)
    if not text:
        return ""
    sentence = text.splitlines()[0].strip()
    if "." in sentence:
        sentence = sentence.split(".", 1)[0].strip()
    return sentence[:180].strip()


def _normalize_key(text):
    return " ".join(_clean_text(text).lower().split())


def _extract_final_answer_incremental(accumulated_text):
    """Extract the current best value of final_answer from a partial JSON string.

    Tries full JSON parse first. On failure, scans for '"final_answer"' and
    extracts the string value character by character, handling escape sequences.
    Returns the decoded string value found so far, or empty string.
    """
    # Try full parse first
    try:
        parsed = json.loads(accumulated_text)
        if isinstance(parsed, dict):
            return parsed.get("final_answer", "")
    except (json.JSONDecodeError, TypeError):
        pass

    # Scan for "final_answer" key
    match = re.search(r'"final_answer"\s*:\s*"', accumulated_text)
    if not match:
        return ""

    pos = match.end()  # right after the opening quote of the value
    result_chars = []
    while pos < len(accumulated_text):
        ch = accumulated_text[pos]
        if ch == "\\" and pos + 1 < len(accumulated_text):
            result_chars.append(ch)
            result_chars.append(accumulated_text[pos + 1])
            pos += 2
        elif ch == '"':
            # Closing quote — complete string value found
            break
        else:
            result_chars.append(ch)
            pos += 1

    raw = "".join(result_chars)
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw


class MagiArbiter:
    def __init__(
        self,
        worker,
        tools=None,
        tool_handler=None,
        max_tool_rounds=4,
        event_listener=None,
        cancel_check=None,
    ):
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

    def _normalize_decision_mode(self, parsed):
        decision_mode = _clean_text(parsed.get("decision_mode")).lower()
        if decision_mode not in VALID_DECISION_MODES:
            return "best_current_branch"
        return decision_mode

    def _normalize_uncertainty_level(self, parsed):
        uncertainty_level = _clean_text(parsed.get("uncertainty_level")).lower()
        if uncertainty_level not in VALID_UNCERTAINTY_LEVELS:
            return "medium"
        return uncertainty_level

    def _normalize_required_fields(self, parsed, raw_text):
        decision_mode = self._normalize_decision_mode(parsed)
        uncertainty_level = self._normalize_uncertainty_level(parsed)
        winning_branch = _clean_text(parsed.get("winning_branch"))
        strongest_surviving_objection = _clean_text(
            parsed.get("strongest_surviving_objection")
        )
        missing_decisive_artifact = _clean_text(parsed.get("missing_decisive_artifact"))
        evidence_sources = _clean_list(parsed.get("evidence_sources"))
        final_answer = _clean_text(parsed.get("final_answer"))
        if not final_answer:
            final_answer = _clean_text(raw_text)

        primary_issue = _clean_text(parsed.get("primary_issue"))
        if not primary_issue:
            primary_issue = (
                winning_branch
                or _first_sentence(final_answer)
                or "Clarify the highest-order issue."
            )

        immediate_obligation = _clean_text(parsed.get("immediate_obligation"))
        if not immediate_obligation:
            if missing_decisive_artifact:
                immediate_obligation = (
                    f"Obtain the decisive artifact: {missing_decisive_artifact}"
                )
            elif uncertainty_level == "high":
                immediate_obligation = (
                    "Resolve the decisive uncertainty before lower-order optimization."
                )
            else:
                immediate_obligation = "Advance the best current branch without losing the higher-order framing."

        if not final_answer:
            final_answer = " ".join(
                part
                for part in [
                    primary_issue,
                    immediate_obligation,
                    f"Missing decisive artifact: {missing_decisive_artifact}"
                    if missing_decisive_artifact
                    else "",
                ]
                if part
            ).strip()

        if (
            primary_issue
            and winning_branch
            and _normalize_key(primary_issue) != _normalize_key(winning_branch)
            and not _normalize_key(final_answer).startswith(
                _normalize_key(primary_issue)
            )
        ):
            separator = "" if primary_issue.endswith((".", "!", "?")) else "."
            final_answer = f"{primary_issue}{separator} {final_answer}".strip()

        return {
            "primary_issue": primary_issue,
            "immediate_obligation": immediate_obligation,
            "decision_mode": decision_mode,
            "uncertainty_level": uncertainty_level,
            "winning_branch": winning_branch,
            "strongest_surviving_objection": strongest_surviving_objection,
            "missing_decisive_artifact": missing_decisive_artifact,
            "evidence_sources": evidence_sources,
            "final_answer": final_answer,
        }

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

        # Arbiter metadata is required. Missing fields fall into deterministic normalization instead of vanishing.
        return self._normalize_required_fields(parsed, raw_text)

    def synthesize(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history,
        memory_snapshot_text,
        deliberation_transcript,
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (
            summarized_conversation_history.summary_text or ""
        ).strip()

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
            history=summarized_conversation_history.recent_turns
            if summarized_conversation_history
            else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=self._forward_worker_event,
            structured_output=True,
            output_schema=MAGI_ARBITER_OUTPUT_SCHEMA,
        )
        invoke_cancel_check(self.cancel_check, "after_model_call:arbiter")
        return self._parse_response(response)

    def synthesize_stream(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history,
        memory_snapshot_text,
        deliberation_transcript,
    ):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (
            summarized_conversation_history.summary_text or ""
        ).strip()

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
            # Build an incremental event listener that extracts final_answer
            # from the streaming JSON and emits text_delta events as content arrives.
            accumulated_text_parts = []
            last_emitted_len = [0]

            def _streaming_listener(event_type, payload):
                if event_type == "text_delta":
                    accumulated_text_parts.append(payload.get("delta", ""))
                    current = _extract_final_answer_incremental(
                        "".join(accumulated_text_parts)
                    )
                    if len(current) > last_emitted_len[0]:
                        new_delta = current[last_emitted_len[0] :]
                        last_emitted_len[0] = len(current)
                        self._emit_event("text_delta", {"delta": new_delta})
                else:
                    self._emit_event(event_type, payload)

            invoke_cancel_check(self.cancel_check, "before_model_call:arbiter")
            response = call_with_optional_cancel_check(
                stream_method,
                cancel_check=self.cancel_check,
                system_prompt=self.system_prompt,
                user_message=user_message,
                history=summarized_conversation_history.recent_turns
                if summarized_conversation_history
                else [],
                tools=self.tools,
                tool_handler=self.tool_handler,
                max_tool_rounds=self.max_tool_rounds,
                event_listener=_streaming_listener,
                structured_output=True,
                output_schema=MAGI_ARBITER_OUTPUT_SCHEMA,
            )
            invoke_cancel_check(self.cancel_check, "after_model_call:arbiter")
            parsed = self._parse_response(response)
            # If the incremental parser missed any tail of final_answer,
            # emit the remaining portion now.
            final = parsed.get("final_answer", "")
            if len(final) > last_emitted_len[0]:
                self._emit_event("text_delta", {"delta": final[last_emitted_len[0] :]})
            return parsed
        return self.synthesize(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
            deliberation_transcript,
        )
