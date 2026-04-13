import json

from orchestration.history_preparer import PreparedHistory
from orchestration.run_control import call_with_optional_cancel_check, invoke_cancel_check
from prompting.magi_prompts import (
    MAGI_CLOSING_PROMPT_TEMPLATE,
    MAGI_DISCUSSION_PROMPT_TEMPLATE,
    MAGI_EAGER_CLOSING_OUTPUT_FORMAT,
    MAGI_EAGER_OUTPUT_FORMAT,
    MAGI_EAGER_SYSTEM_PROMPT,
    MAGI_HISTORIAN_CLOSING_OUTPUT_FORMAT,
    MAGI_HISTORIAN_OUTPUT_FORMAT,
    MAGI_HISTORIAN_SYSTEM_PROMPT,
    MAGI_NET_NEW_INSTRUCTION,
    MAGI_SKEPTIC_CLOSING_OUTPUT_FORMAT,
    MAGI_SKEPTIC_OUTPUT_FORMAT,
    MAGI_SKEPTIC_SYSTEM_PROMPT,
    ROLE_REMINDERS,
)

PHASE_OPENING = "opening_arguments"
PHASE_DISCUSSION = "discussion"
PHASE_CLOSING = "closing_arguments"

VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_GROUNDING_STRENGTH = {"strong", "weak", "absent", "conflicted"}
VALID_BRANCH_SUPPORT_STATUS = {"supports", "weakens", "absent", "conflicted"}
VALID_ENVIRONMENT_FIT = {"aligned", "mismatch", "unknown"}
VALID_DISCUSSION_MODES = {"optional", "forced"}

NO_DELTA_REASON_FALLBACKS = {
    "unresolved_issue_unchanged": "The unresolved issue still stands.",
    "absorbed_by_other_role": "Another role already surfaced the meaningful delta.",
    "blocked_by_missing_evidence": "Missing evidence still blocks a stronger update.",
    "no_grounding_change": "The evidence bundle did not materially change.",
    "no_structured_delta": "No material delta emerged from the current evidence.",
    "forced_round_blank_output": "The response stayed blank, so the stance remains unchanged pending new evidence.",
}

NO_DELTA_PREFIXES = {
    "eager": "Branch unchanged.",
    "skeptic": "Objection unchanged.",
    "historian": "Grounding unchanged.",
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


def _first_text(parsed, *keys):
    for key in keys:
        text = _clean_text(parsed.get(key))
        if text:
            return text
    return ""


def _is_meaningful_list(parsed, key):
    return bool(_clean_list(parsed.get(key)))


def _normalize_confidence(parsed):
    confidence = _clean_text(parsed.get("confidence")).lower()
    if confidence not in VALID_CONFIDENCE:
        return "medium"
    return confidence


class MagiRole:
    role_name = "role"
    system_prompt = ""
    discussion_output_format = ""
    closing_output_format = ""

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

    def _build_context_bundle(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, evidence_pool_summary=""):
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        pool_section = ""
        if evidence_pool_summary:
            net_new_note = MAGI_NET_NEW_INSTRUCTION
            pool_section = f"\n{evidence_pool_summary}\nNote: {net_new_note}\n"
        return f"""
PRIOR CONVERSATION SUMMARY:
{history_summary_text}

KNOWN SYSTEM MEMORY:
{memory_snapshot_text}
{pool_section}
REFERENCE CONTEXT:
{retrieved_docs}

USER QUESTION:
{user_query}
""".strip()

    def _build_opening_message(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, evidence_pool_summary=""):
        return self._build_context_bundle(
            user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
            evidence_pool_summary=evidence_pool_summary,
        )

    def _base_payload(self, parsed):
        return {
            "position": _clean_text(parsed.get("position")),
            "confidence": _normalize_confidence(parsed),
            "key_claims": _clean_list(parsed.get("key_claims")),
            "evidence_sources": _clean_list(parsed.get("evidence_sources")),
        }

    def _discussion_mode(self, discussion_mode):
        normalized = _clean_text(discussion_mode).lower()
        if normalized not in VALID_DISCUSSION_MODES:
            return "optional"
        return normalized

    def _raw_has_delta(self, parsed):
        raise NotImplementedError

    def _normalize_opening_payload(self, parsed):
        raise NotImplementedError

    def _normalize_closing_payload(self, parsed):
        raise NotImplementedError

    def _build_no_delta_position(self, no_delta_reason, unresolved_issue):
        prefix = NO_DELTA_PREFIXES.get(self.role_name, "Stance unchanged.")
        reason = NO_DELTA_REASON_FALLBACKS.get(no_delta_reason, NO_DELTA_REASON_FALLBACKS["no_structured_delta"])
        if unresolved_issue:
            return f"{prefix} {reason} Focus remains on: {unresolved_issue}"
        return f"{prefix} {reason}"

    def _normalize_discussion_payload(self, parsed, discussion_mode="optional", unresolved_issue=""):
        normalized = self._normalize_opening_payload(parsed)
        has_delta = self._raw_has_delta(parsed)
        discussion_mode = self._discussion_mode(discussion_mode)
        explicit_new_information = parsed.get("new_information") if isinstance(parsed, dict) else None
        if explicit_new_information is None:
            new_information = has_delta
        else:
            new_information = _clean_bool(explicit_new_information, default=has_delta)
            if new_information and not has_delta:
                new_information = False
            elif not new_information and has_delta and not _clean_text(parsed.get("no_delta_reason")):
                new_information = True

        normalized["new_information"] = new_information
        normalized["discussion_mode"] = discussion_mode
        normalized["no_delta_reason"] = _clean_text(parsed.get("no_delta_reason"))

        # Forced rounds must stay inspectable even when the model has no delta.
        if not normalized["new_information"] and not normalized["no_delta_reason"]:
            normalized["no_delta_reason"] = (
                "forced_round_blank_output" if discussion_mode == "forced" else "no_structured_delta"
            )

        if discussion_mode == "forced" and not normalized["new_information"] and not normalized["position"]:
            normalized["position"] = self._build_no_delta_position(
                normalized["no_delta_reason"], unresolved_issue,
            )

        return normalized

    def _parse_json(self, raw_text):
        raw_text = _clean_text(raw_text)
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            raw_text = "\n".join(lines).strip()
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                return parsed, raw_text
        except (json.JSONDecodeError, TypeError):
            pass
        return {"position": raw_text}, raw_text

    def _parse_response(self, raw_text, phase, discussion_mode="optional", unresolved_issue=""):
        parsed, _ = self._parse_json(raw_text)
        # Phase-aware parsing keeps Skeptic/Historian from being collapsed into an Eager-style branch picker.
        if phase == PHASE_OPENING:
            normalized = self._normalize_opening_payload(parsed)
        elif phase == PHASE_DISCUSSION:
            normalized = self._normalize_discussion_payload(parsed, discussion_mode, unresolved_issue)
        elif phase == PHASE_CLOSING:
            normalized = self._normalize_closing_payload(parsed)
        else:
            normalized = self._normalize_opening_payload(parsed)
        normalized["phase"] = phase
        normalized["role"] = self.role_name
        return normalized

    def _get_generate_fn(self, event_listener):
        """Use generate_text_stream when a custom listener is provided and the worker supports it."""
        if event_listener is not None:
            stream_fn = getattr(self.worker, "generate_text_stream", None)
            if callable(stream_fn):
                return stream_fn
        return self.worker.generate_text

    def opening_argument(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
        event_listener=None,
        evidence_pool_summary="",
        enable_web_search=False,
    ):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        user_message = self._build_opening_message(
            user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
            evidence_pool_summary=evidence_pool_summary,
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
            enable_web_search=enable_web_search,
            event_listener=listener,
        )
        invoke_cancel_check(self.cancel_check, f"after_model_call:{self.role_name}:opening")
        return self._parse_response(raw, PHASE_OPENING)

    def discuss(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history=None,
        memory_snapshot_text="",
        transcript="",
        round_number=1,
        discussion_mode="optional",
        unresolved_issue="",
        user_intervention_block="none",
        event_listener=None,
        evidence_pool_summary="",
        enable_web_search=False,
    ):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        history_summary_text = (summarized_conversation_history.summary_text or "").strip()
        pool_block = ""
        if evidence_pool_summary:
            pool_block = f"{evidence_pool_summary}\nNote: {MAGI_NET_NEW_INSTRUCTION}"
        discussion_prompt = MAGI_DISCUSSION_PROMPT_TEMPLATE.format(
            role_name=self.role_name.upper(),
            round_number=round_number,
            discussion_mode=self._discussion_mode(discussion_mode),
            unresolved_issue=unresolved_issue or "No unresolved issue was provided.",
            user_query=user_query,
            history_summary_text=history_summary_text,
            memory_snapshot_text=memory_snapshot_text,
            evidence_pool_summary=pool_block,
            retrieved_docs=retrieved_docs,
            transcript=transcript,
            user_intervention_block=user_intervention_block or "none",
            role_reminder=ROLE_REMINDERS.get(self.role_name, ""),
            role_output_format=self.discussion_output_format,
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
            enable_web_search=enable_web_search,
            event_listener=listener,
        )
        invoke_cancel_check(self.cancel_check, f"after_model_call:{self.role_name}:discussion")
        return self._parse_response(
            raw,
            PHASE_DISCUSSION,
            discussion_mode=discussion_mode,
            unresolved_issue=unresolved_issue,
        )

    def closing_argument(self, user_query, transcript, event_listener=None):
        listener = event_listener if event_listener is not None else self._forward_worker_event
        closing_prompt = MAGI_CLOSING_PROMPT_TEMPLATE.format(
            role_name=self.role_name.upper(),
            role_reminder=ROLE_REMINDERS.get(self.role_name, ""),
            user_query=user_query,
            transcript=transcript,
            role_output_format=self.closing_output_format,
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
        return self._parse_response(raw, PHASE_CLOSING)

    def _forward_worker_event(self, event_type, payload):
        self._emit_event(event_type, payload)


class MagiEager(MagiRole):
    role_name = "eager"
    system_prompt = MAGI_EAGER_SYSTEM_PROMPT
    discussion_output_format = MAGI_EAGER_OUTPUT_FORMAT
    closing_output_format = MAGI_EAGER_CLOSING_OUTPUT_FORMAT

    def _raw_has_delta(self, parsed):
        return any((
            _first_text(parsed, "position"),
            _first_text(parsed, "primary_issue"),
            _first_text(parsed, "immediate_obligation"),
            _first_text(parsed, "provisional_branch", "branch"),
            _first_text(parsed, "best_next_check"),
            _first_text(parsed, "strongest_caveat", "strongest_objection"),
            _first_text(parsed, "missing_decisive_artifact"),
            _clean_list(parsed.get("key_claims")),
            _clean_list(parsed.get("evidence_sources")),
        ))

    def _normalize_opening_payload(self, parsed):
        normalized = self._base_payload(parsed)
        provisional_branch = _first_text(parsed, "provisional_branch", "branch")
        strongest_caveat = _first_text(parsed, "strongest_caveat", "strongest_objection")
        missing_decisive_artifact = _first_text(parsed, "missing_decisive_artifact")
        normalized.update({
            "primary_issue": _first_text(parsed, "primary_issue"),
            "immediate_obligation": _first_text(parsed, "immediate_obligation"),
            "provisional_branch": provisional_branch,
            "best_next_check": _first_text(parsed, "best_next_check"),
            "strongest_caveat": strongest_caveat,
            "missing_decisive_artifact": missing_decisive_artifact,
            "branch": provisional_branch,
            "strongest_objection": strongest_caveat,
        })
        return normalized

    def _normalize_closing_payload(self, parsed):
        normalized = self._base_payload(parsed)
        provisional_branch = _first_text(parsed, "provisional_branch", "branch")
        strongest_caveat = _first_text(parsed, "strongest_caveat", "strongest_objection")
        normalized.update({
            "provisional_branch": provisional_branch,
            "changed_since_opening": _clean_bool(parsed.get("changed_since_opening"), default=False),
            "best_next_check": _first_text(parsed, "best_next_check"),
            "strongest_caveat": strongest_caveat,
            "missing_decisive_artifact": _first_text(parsed, "missing_decisive_artifact"),
            "branch": provisional_branch,
            "strongest_objection": strongest_caveat,
        })
        return normalized


class MagiSkeptic(MagiRole):
    role_name = "skeptic"
    system_prompt = MAGI_SKEPTIC_SYSTEM_PROMPT
    discussion_output_format = MAGI_SKEPTIC_OUTPUT_FORMAT
    closing_output_format = MAGI_SKEPTIC_CLOSING_OUTPUT_FORMAT

    def _raw_has_delta(self, parsed):
        return any((
            _first_text(parsed, "position"),
            _first_text(parsed, "target_branch", "branch"),
            _first_text(parsed, "weakest_assumption"),
            _first_text(parsed, "strongest_objection", "strongest_caveat"),
            _first_text(parsed, "counterframe"),
            _first_text(parsed, "falsifying_check", "best_next_check"),
            _first_text(parsed, "blocking_missing_artifact", "missing_decisive_artifact"),
            _clean_list(parsed.get("key_claims")),
            _clean_list(parsed.get("evidence_sources")),
        ))

    def _normalize_opening_payload(self, parsed):
        normalized = self._base_payload(parsed)
        target_branch = _first_text(parsed, "target_branch", "branch")
        falsifying_check = _first_text(parsed, "falsifying_check", "best_next_check")
        strongest_objection = _first_text(parsed, "strongest_objection", "strongest_caveat")
        blocking_missing_artifact = _first_text(parsed, "blocking_missing_artifact", "missing_decisive_artifact")
        normalized.update({
            "target_branch": target_branch,
            "weakest_assumption": _first_text(parsed, "weakest_assumption"),
            "strongest_objection": strongest_objection,
            "counterframe": _first_text(parsed, "counterframe"),
            "falsifying_check": falsifying_check,
            "blocking_missing_artifact": blocking_missing_artifact,
            "branch": target_branch,
            "best_next_check": falsifying_check,
            "missing_decisive_artifact": blocking_missing_artifact,
        })
        return normalized

    def _normalize_closing_payload(self, parsed):
        normalized = self._base_payload(parsed)
        target_branch = _first_text(parsed, "target_branch", "branch")
        falsifying_check = _first_text(parsed, "falsifying_check", "best_next_check")
        blocking_missing_artifact = _first_text(parsed, "blocking_missing_artifact", "missing_decisive_artifact")
        normalized.update({
            "target_branch": target_branch,
            "changed_since_opening": _clean_bool(parsed.get("changed_since_opening"), default=False),
            "strongest_objection": _first_text(parsed, "strongest_objection", "strongest_caveat"),
            "falsifying_check": falsifying_check,
            "blocking_missing_artifact": blocking_missing_artifact,
            "branch": target_branch,
            "best_next_check": falsifying_check,
            "missing_decisive_artifact": blocking_missing_artifact,
        })
        return normalized


class MagiHistorian(MagiRole):
    role_name = "historian"
    system_prompt = MAGI_HISTORIAN_SYSTEM_PROMPT
    discussion_output_format = MAGI_HISTORIAN_OUTPUT_FORMAT
    closing_output_format = MAGI_HISTORIAN_CLOSING_OUTPUT_FORMAT

    def _raw_has_delta(self, parsed):
        return any((
            _first_text(parsed, "position"),
            _first_text(parsed, "evaluated_branch", "branch"),
            _first_text(parsed, "grounding_strength"),
            _first_text(parsed, "branch_support_status"),
            _first_text(parsed, "most_relevant_evidence"),
            _first_text(parsed, "most_important_gap", "missing_decisive_artifact"),
            _is_meaningful_list(parsed, "memory_facts"),
            _is_meaningful_list(parsed, "doc_support"),
            _is_meaningful_list(parsed, "attempt_history"),
            _is_meaningful_list(parsed, "operator_warnings"),
            _clean_list(parsed.get("evidence_sources")),
        ))

    def _normalize_grounding_strength(self, parsed):
        grounding_strength = _first_text(parsed, "grounding_strength").lower()
        if grounding_strength in VALID_GROUNDING_STRENGTH:
            return grounding_strength
        has_any_grounding = any((
            _clean_list(parsed.get("memory_facts")),
            _clean_list(parsed.get("doc_support")),
            _clean_list(parsed.get("attempt_history")),
            _clean_list(parsed.get("evidence_sources")),
        ))
        return "weak" if has_any_grounding else "absent"

    def _normalize_branch_support_status(self, parsed, grounding_strength):
        branch_support_status = _first_text(parsed, "branch_support_status").lower()
        if branch_support_status in VALID_BRANCH_SUPPORT_STATUS:
            return branch_support_status
        if grounding_strength == "strong":
            return "supports"
        if grounding_strength == "conflicted":
            return "conflicted"
        if grounding_strength == "absent":
            return "absent"
        return "weakens"

    def _normalize_environment_fit(self, parsed):
        environment_fit = _first_text(parsed, "environment_fit").lower()
        if environment_fit in VALID_ENVIRONMENT_FIT:
            return environment_fit
        return "unknown"

    def _normalize_opening_payload(self, parsed):
        normalized = self._base_payload(parsed)
        grounding_strength = self._normalize_grounding_strength(parsed)
        branch_support_status = self._normalize_branch_support_status(parsed, grounding_strength)
        evaluated_branch = _first_text(parsed, "evaluated_branch", "branch")
        most_relevant_evidence = _first_text(parsed, "most_relevant_evidence")
        if not most_relevant_evidence:
            most_relevant_evidence = _first_text(
                {
                    "candidate_1": (_clean_list(parsed.get("memory_facts")) or [""])[0],
                    "candidate_2": (_clean_list(parsed.get("doc_support")) or [""])[0],
                    "candidate_3": (_clean_list(parsed.get("attempt_history")) or [""])[0],
                },
                "candidate_1",
                "candidate_2",
                "candidate_3",
            )
        most_important_gap = _first_text(parsed, "most_important_gap", "missing_decisive_artifact")
        normalized.update({
            "evaluated_branch": evaluated_branch,
            "grounding_strength": grounding_strength,
            "branch_support_status": branch_support_status,
            "memory_facts": _clean_list(parsed.get("memory_facts")),
            "doc_support": _clean_list(parsed.get("doc_support")),
            "attempt_history": _clean_list(parsed.get("attempt_history")),
            "environment_fit": self._normalize_environment_fit(parsed),
            "operator_warnings": _clean_list(parsed.get("operator_warnings")),
            "most_relevant_evidence": most_relevant_evidence,
            "most_important_gap": most_important_gap,
            "branch": evaluated_branch,
            "best_next_check": "",
            "strongest_objection": most_important_gap,
            "missing_decisive_artifact": most_important_gap,
        })
        return normalized

    def _normalize_closing_payload(self, parsed):
        normalized = self._base_payload(parsed)
        grounding_strength = self._normalize_grounding_strength(parsed)
        branch_support_status = self._normalize_branch_support_status(parsed, grounding_strength)
        evaluated_branch = _first_text(parsed, "evaluated_branch", "branch")
        most_important_gap = _first_text(parsed, "most_important_gap", "missing_decisive_artifact")
        normalized.update({
            "evaluated_branch": evaluated_branch,
            "changed_since_opening": _clean_bool(parsed.get("changed_since_opening"), default=False),
            "grounding_strength": grounding_strength,
            "branch_support_status": branch_support_status,
            "most_relevant_evidence": _first_text(parsed, "most_relevant_evidence"),
            "most_important_gap": most_important_gap,
            "branch": evaluated_branch,
            "best_next_check": "",
            "strongest_objection": most_important_gap,
            "missing_decisive_artifact": most_important_gap,
        })
        return normalized
