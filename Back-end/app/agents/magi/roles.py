import json

from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import call_with_optional_cancel_check, invoke_cancel_check
from prompting.magi_prompts import (
    MAGI_CLOSING_PROMPT_TEMPLATE,
    MAGI_DISCUSSION_PROMPT_TEMPLATE,
    MAGI_EAGER_SYSTEM_PROMPT,
    MAGI_HISTORIAN_SYSTEM_PROMPT,
    MAGI_SKEPTIC_SYSTEM_PROMPT,
    ROLE_REMINDERS,
)

VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_GROUNDING_STRENGTH = {"strong", "weak", "absent", "conflicted"}
VALID_ENVIRONMENT_FIT = {"aligned", "mismatch", "unknown"}


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


def _clean_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return default


class MagiRole:
    role_name = "role"
    system_prompt = ""

    def __init__(self, worker, tools=None, tool_handler=None, max_tool_rounds=4, event_listener=None, cancel_check=None):
        self.worker = worker
        self.tools = tools or []
        self.tool_handler = tool_handler
        self.max_tool_rounds = max_tool_rounds
        self.event_listener = event_listener
        self.cancel_check = cancel_check

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

    def _derive_branch(self, parsed):
        branch = _clean_text(parsed.get("branch"))
        if branch:
            return branch
        key_claims = _clean_list(parsed.get("key_claims"))
        if key_claims:
            return key_claims[0][:120]
        best_next_check = _clean_text(parsed.get("best_next_check"))
        if best_next_check:
            return best_next_check[:120]
        position = _clean_text(parsed.get("position"))
        if not position:
            return ""
        return position.splitlines()[0][:120].strip()

    def _normalize_common_fields(self, parsed):
        confidence = _clean_text(parsed.get("confidence")).lower()
        if confidence not in VALID_CONFIDENCE:
            confidence = "medium"

        normalized = {
            "branch": self._derive_branch(parsed),
            "position": _clean_text(parsed.get("position")),
            "confidence": confidence,
            "key_claims": _clean_list(parsed.get("key_claims")),
            "best_next_check": _clean_text(parsed.get("best_next_check")),
            "strongest_objection": _clean_text(parsed.get("strongest_objection")),
            "missing_decisive_artifact": _clean_text(parsed.get("missing_decisive_artifact")),
            "missing_evidence": _clean_list(parsed.get("missing_evidence")),
            "evidence_sources": _clean_list(parsed.get("evidence_sources")),
        }
        if not normalized["missing_decisive_artifact"] and normalized["missing_evidence"]:
            normalized["missing_decisive_artifact"] = normalized["missing_evidence"][0]
        return normalized

    def _normalize_historian_fields(self, parsed):
        grounding_strength = _clean_text(parsed.get("grounding_strength")).lower()
        if grounding_strength not in VALID_GROUNDING_STRENGTH:
            has_any_grounding = any(
                (
                    _clean_list(parsed.get("memory_facts")),
                    _clean_list(parsed.get("doc_support")),
                    _clean_list(parsed.get("attempt_history")),
                    _clean_list(parsed.get("evidence_sources")),
                )
            )
            grounding_strength = "weak" if has_any_grounding else "absent"

        environment_fit = _clean_text(parsed.get("environment_fit")).lower()
        if environment_fit not in VALID_ENVIRONMENT_FIT:
            environment_fit = "unknown"

        return {
            "grounding_strength": grounding_strength,
            "memory_facts": _clean_list(parsed.get("memory_facts")),
            "doc_support": _clean_list(parsed.get("doc_support")),
            "attempt_history": _clean_list(parsed.get("attempt_history")),
            "environment_fit": environment_fit,
            "operator_warnings": _clean_list(parsed.get("operator_warnings")),
        }

    def _normalize_payload(self, parsed):
        normalized = self._normalize_common_fields(parsed)
        normalized["new_information"] = _clean_bool(
            parsed.get("new_information"),
            default=bool(
                normalized["position"]
                or normalized["strongest_objection"]
                or normalized["best_next_check"]
                or normalized["missing_decisive_artifact"]
            ),
        )
        normalized["changed_since_opening"] = _clean_bool(
            parsed.get("changed_since_opening"),
            default=bool(
                normalized["position"]
                or normalized["strongest_objection"]
                or normalized["best_next_check"]
            ),
        )
        if self.role_name == "historian":
            normalized.update(self._normalize_historian_fields(parsed))
        return normalized

    def _parse_response(self, raw_text):
        raw_text = (raw_text or "").strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines).strip()
        parsed = None
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                return self._normalize_payload(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return self._normalize_payload({
            "position": raw_text,
        })

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
        invoke_cancel_check(self.cancel_check, f"before_model_call:{self.role_name}:opening")
        raw = call_with_optional_cancel_check(
            self._get_generate_fn(event_listener),
            cancel_check=self.cancel_check,
            system_prompt=self.system_prompt,
            user_message=user_message,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=listener,
        )
        invoke_cancel_check(self.cancel_check, f"after_model_call:{self.role_name}:opening")
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
        invoke_cancel_check(self.cancel_check, f"before_model_call:{self.role_name}:discussion")
        raw = call_with_optional_cancel_check(
            self._get_generate_fn(event_listener),
            cancel_check=self.cancel_check,
            system_prompt=self.system_prompt,
            user_message=discussion_prompt,
            history=summarized_conversation_history.recent_turns if summarized_conversation_history else [],
            tools=self.tools,
            tool_handler=self.tool_handler,
            max_tool_rounds=self.max_tool_rounds,
            event_listener=listener,
        )
        invoke_cancel_check(self.cancel_check, f"after_model_call:{self.role_name}:discussion")
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
        invoke_cancel_check(self.cancel_check, f"before_model_call:{self.role_name}:closing")
        raw = call_with_optional_cancel_check(
            self._get_generate_fn(event_listener),
            cancel_check=self.cancel_check,
            system_prompt=self.system_prompt,
            user_message=closing_prompt,
            history=[],
            tools=[],
            tool_handler=None,
            max_tool_rounds=0,
            event_listener=listener,
        )
        invoke_cancel_check(self.cancel_check, f"after_model_call:{self.role_name}:closing")
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
