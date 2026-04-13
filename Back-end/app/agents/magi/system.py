import json
from enum import Enum, auto

from orchestration.run_control import RunPausedError, invoke_cancel_check


class MagiState(Enum):
    OPENING_ARGUMENTS = auto()
    ROLE_EAGER = auto()
    ROLE_SKEPTIC = auto()
    ROLE_HISTORIAN = auto()
    DISCUSSION_GATE = auto()
    DISCUSSION = auto()
    DISCUSSION_EAGER = auto()
    DISCUSSION_SKEPTIC = auto()
    DISCUSSION_HISTORIAN = auto()
    CLOSING_ARGUMENTS = auto()
    CLOSING_EAGER = auto()
    CLOSING_SKEPTIC = auto()
    CLOSING_HISTORIAN = auto()
    ARBITER = auto()
    COMPLETE = auto()
    ERROR = auto()


MAX_POSITION_CHARS = 1500


class MagiSystem:
    def __init__(
        self,
        eager,
        skeptic,
        historian,
        arbiter,
        max_discussion_rounds=2,
        state_listener=None,
        event_listener=None,
        cancel_check=None,
        pause_check=None,
        historian_web_search_decider=None,
    ):
        self.eager = eager
        self.skeptic = skeptic
        self.historian = historian
        self.arbiter = arbiter
        self.max_discussion_rounds = max_discussion_rounds
        self.state_listener = state_listener
        self.event_listener = event_listener
        self.last_council_entries = []
        self.last_arbiter_metadata = {}
        self.cancel_check = cancel_check
        self.pause_check = pause_check
        self.last_pause_state = {}
        self.historian_web_search_decider = historian_web_search_decider

    def _set_state(self, state, payload=None):
        if self.state_listener is not None:
            self.state_listener(state, payload or {})

    def _emit_event(self, event_type, payload):
        if self.event_listener is not None:
            self.event_listener(event_type, payload)

    def _truncate(self, text, max_chars=MAX_POSITION_CHARS):
        text = text or ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3].rstrip() + "..."

    def _make_role_streamer_listener(self, role_name, phase, round_number=None):
        """Forward non-text provider events; visible role text is emitted only from parsed payloads."""

        def listener(event_type, payload):
            if event_type == "text_delta":
                return
            self._emit_event(event_type, payload)
        return listener

    def _emit_role_text(self, role_name, phase, position_text, round_number=None):
        visible_text = str(position_text or "")
        if not visible_text:
            return
        self._emit_event("magi_role_text_delta", {
            "role": role_name,
            "phase": phase,
            "round": round_number,
            "delta": visible_text,
        })

    def _check_cancel(self, checkpoint):
        invoke_cancel_check(self.cancel_check, checkpoint)

    def _should_enable_historian_web_search(self, *, phase, round_number, unresolved_issue):
        if not callable(self.historian_web_search_decider):
            return False
        return bool(
            self.historian_web_search_decider(
                phase=phase,
                round_number=round_number,
                unresolved_issue=unresolved_issue,
            )
        )

    def _maybe_pause(self, checkpoint, pause_state_factory):
        self._check_cancel(f"before_pause_checkpoint:{checkpoint}")
        if self.pause_check is None:
            return
        if not bool(self.pause_check(checkpoint)):
            return
        pause_state = pause_state_factory() if callable(pause_state_factory) else dict(pause_state_factory or {})
        self.last_pause_state = dict(pause_state or {})
        raise RunPausedError(
            "Run paused.",
            pause_state=self.last_pause_state,
            payload={
                "message": "Run paused.",
                "checkpoint": checkpoint,
            },
        )

    def _normalize_branch_key(self, value):
        return " ".join(str(value or "").strip().lower().split())

    def _find_role_payload(self, positions, role_name):
        for candidate_role, parsed in positions:
            if candidate_role == role_name:
                return parsed
        return {}

    def _latest_role_payloads(self, opening_positions, discussion_rounds):
        latest = {role_name: parsed for role_name, parsed in opening_positions}
        for round_positions in discussion_rounds:
            for role_name, parsed in round_positions:
                latest[role_name] = parsed
        return latest

    def _openings_materially_diverge(self, opening_positions):
        eager = self._find_role_payload(opening_positions, "eager")
        skeptic = self._find_role_payload(opening_positions, "skeptic")
        historian = self._find_role_payload(opening_positions, "historian")

        eager_branch = self._normalize_branch_key(eager.get("provisional_branch") or eager.get("branch"))
        eager_issue = self._normalize_branch_key(eager.get("primary_issue"))
        eager_obligation = self._normalize_branch_key(eager.get("immediate_obligation"))
        skeptic_target = self._normalize_branch_key(skeptic.get("target_branch") or skeptic.get("branch"))
        skeptic_counterframe = self._normalize_branch_key(skeptic.get("counterframe"))
        historian_branch = self._normalize_branch_key(historian.get("evaluated_branch") or historian.get("branch"))

        if eager_branch and skeptic_target and eager_branch != skeptic_target:
            return True
        if eager_branch and historian_branch and eager_branch != historian_branch:
            return True
        if skeptic_counterframe and skeptic_counterframe not in {eager_issue, eager_obligation, eager_branch}:
            return True
        return False

    def _discussion_gate(self, opening_positions):
        historian = self._find_role_payload(opening_positions, "historian")
        grounding_strength = historian.get("grounding_strength", "absent")
        materially_divergent = self._openings_materially_diverge(opening_positions)
        force_due_to_grounding = grounding_strength in {"weak", "absent", "conflicted"}
        force_discussion = materially_divergent or force_due_to_grounding
        if materially_divergent:
            reason = "material_opening_divergence"
        elif force_due_to_grounding:
            reason = f"grounding_{grounding_strength}"
        else:
            reason = "aligned_openings_with_strong_grounding"
        return {
            "force_discussion": force_discussion,
            "discussion_mode": "forced" if force_discussion else "optional",
            "materially_divergent_openings": materially_divergent,
            "grounding_strength": grounding_strength,
            "reason": reason,
        }

    def _determine_unresolved_issue(self, opening_positions, discussion_rounds):
        latest = self._latest_role_payloads(opening_positions, discussion_rounds)
        eager = latest.get("eager", {})
        skeptic = latest.get("skeptic", {})
        historian = latest.get("historian", {})

        primary_issue = (eager.get("primary_issue") or "").strip()
        provisional_branch = (eager.get("provisional_branch") or eager.get("branch") or "").strip()
        if primary_issue and self._normalize_branch_key(primary_issue) != self._normalize_branch_key(provisional_branch):
            return f"Primary issue: {primary_issue}"

        immediate_obligation = (eager.get("immediate_obligation") or "").strip()
        if immediate_obligation:
            return f"Immediate obligation: {immediate_obligation}"

        counterframe = (skeptic.get("counterframe") or "").strip()
        if counterframe:
            return f"Counterframe: {counterframe}"

        strongest_objection = (skeptic.get("strongest_objection") or "").strip()
        if strongest_objection:
            return f"Surviving objection: {strongest_objection}"

        grounding_strength = (historian.get("grounding_strength") or "").strip()
        if grounding_strength in {"weak", "absent", "conflicted"}:
            grounding_gap = (
                historian.get("most_important_gap")
                or historian.get("missing_decisive_artifact")
                or historian.get("position")
                or ""
            ).strip()
            if grounding_gap:
                return f"Grounding gap ({grounding_strength}): {grounding_gap}"
            return f"Grounding remains {grounding_strength} for the current branch."

        missing_artifact = (
            eager.get("missing_decisive_artifact")
            or skeptic.get("blocking_missing_artifact")
            or historian.get("most_important_gap")
            or ""
        ).strip()
        if missing_artifact:
            return f"Missing decisive artifact: {missing_artifact}"

        best_next_check = (
            eager.get("best_next_check")
            or skeptic.get("falsifying_check")
            or ""
        ).strip()
        if best_next_check:
            return f"Best next check: {best_next_check}"

        if provisional_branch:
            return f"Clarify whether the branch '{provisional_branch}' is actually supported."

        return "Clarify the strongest unresolved issue before lower-order optimization."

    def _structured_transcript_payload(self, role_name, parsed):
        entry = {
            "role": role_name,
            "phase": parsed.get("phase", ""),
            "confidence": parsed.get("confidence", ""),
            "position": parsed.get("position", ""),
        }

        if role_name == "eager":
            entry.update({
                "primary_issue": parsed.get("primary_issue", ""),
                "immediate_obligation": parsed.get("immediate_obligation", ""),
                "provisional_branch": parsed.get("provisional_branch", ""),
                "best_next_check": parsed.get("best_next_check", ""),
                "strongest_caveat": parsed.get("strongest_caveat", ""),
                "missing_decisive_artifact": parsed.get("missing_decisive_artifact", ""),
                "key_claims": parsed.get("key_claims", []),
                "evidence_sources": parsed.get("evidence_sources", []),
            })
        elif role_name == "skeptic":
            entry.update({
                "target_branch": parsed.get("target_branch", ""),
                "weakest_assumption": parsed.get("weakest_assumption", ""),
                "strongest_objection": parsed.get("strongest_objection", ""),
                "counterframe": parsed.get("counterframe", ""),
                "falsifying_check": parsed.get("falsifying_check", ""),
                "blocking_missing_artifact": parsed.get("blocking_missing_artifact", ""),
                "key_claims": parsed.get("key_claims", []),
                "evidence_sources": parsed.get("evidence_sources", []),
            })
        elif role_name == "historian":
            entry.update({
                "evaluated_branch": parsed.get("evaluated_branch", ""),
                "grounding_strength": parsed.get("grounding_strength", ""),
                "branch_support_status": parsed.get("branch_support_status", ""),
                "memory_facts": parsed.get("memory_facts", []),
                "doc_support": parsed.get("doc_support", []),
                "attempt_history": parsed.get("attempt_history", []),
                "environment_fit": parsed.get("environment_fit", ""),
                "operator_warnings": parsed.get("operator_warnings", []),
                "most_relevant_evidence": parsed.get("most_relevant_evidence", ""),
                "most_important_gap": parsed.get("most_important_gap", ""),
                "evidence_sources": parsed.get("evidence_sources", []),
            })

        if parsed.get("phase") == "discussion":
            entry["new_information"] = parsed.get("new_information", False)
            if parsed.get("no_delta_reason"):
                entry["no_delta_reason"] = parsed.get("no_delta_reason")

        if parsed.get("phase") == "closing_arguments":
            entry["changed_since_opening"] = parsed.get("changed_since_opening", False)

        return {key: value for key, value in entry.items() if value not in ("", [], None)}

    def _format_position(self, role_name, parsed):
        # The arbiter needs compact structured state, not only flattened prose.
        return json.dumps(
            self._structured_transcript_payload(role_name, parsed),
            ensure_ascii=True,
            indent=2,
            sort_keys=False,
        )

    def _normalize_interventions(self, interventions):
        normalized = []
        for entry in interventions or []:
            if not isinstance(entry, dict):
                continue
            try:
                round_number = None if entry.get("round") is None else int(entry.get("round"))
            except (TypeError, ValueError):
                round_number = None
            try:
                after_role_count = int(entry.get("after_role_count", 0) or 0)
            except (TypeError, ValueError):
                after_role_count = 0
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            normalized.append({
                "entry_kind": "user_intervention",
                "role": "user",
                "phase": "discussion",
                "round": round_number,
                "after_role_count": max(0, after_role_count),
                "input_kind": str(entry.get("input_kind") or "fact"),
                "text": text,
            })
        return normalized

    def _interventions_by_round(self, interventions):
        grouped = {}
        for entry in self._normalize_interventions(interventions):
            try:
                round_number = int(entry.get("round") or 1)
            except (TypeError, ValueError):
                round_number = 1
            grouped.setdefault(round_number, []).append(entry)
        for round_entries in grouped.values():
            round_entries.sort(key=lambda item: int(item.get("after_role_count", 0) or 0))
        return grouped

    def _format_intervention(self, intervention):
        return json.dumps(
            {
                "entry_kind": "user_intervention",
                "input_kind": intervention.get("input_kind", "fact"),
                "position": intervention.get("text", ""),
            },
            ensure_ascii=True,
            indent=2,
            sort_keys=False,
        )

    def _build_transcript(self, opening_positions, discussion_rounds, closing_positions=None, interventions=None):
        sections = ["=== OPENING ARGUMENTS ==="]
        for role_name, parsed in opening_positions:
            sections.append(self._truncate(self._format_position(role_name, parsed)))
            sections.append("")

        interventions_by_round = self._interventions_by_round(interventions)
        for round_num, round_positions in enumerate(discussion_rounds, 1):
            sections.append(f"=== DISCUSSION ROUND {round_num} ===")
            pending_interventions = list(interventions_by_round.get(round_num, []))

            def _append_interventions(after_role_count):
                while pending_interventions and int(pending_interventions[0].get("after_role_count", 0) or 0) <= after_role_count:
                    sections.append(self._truncate(self._format_intervention(pending_interventions.pop(0))))
                    sections.append("")

            _append_interventions(0)
            rendered_roles = 0
            for role_name, parsed in round_positions:
                sections.append(self._truncate(self._format_position(role_name, parsed)))
                sections.append("")
                rendered_roles += 1
                _append_interventions(rendered_roles)
            _append_interventions(999)

        if closing_positions:
            sections.append("=== CLOSING ARGUMENTS ===")
            for role_name, parsed in closing_positions:
                sections.append(self._truncate(self._format_position(role_name, parsed)))
                sections.append("")

        return "\n".join(sections).strip()

    def _run_opening_arguments(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, evidence_pool_summary=""):
        self._check_cancel("before_magi_opening_arguments")
        self._set_state(MagiState.OPENING_ARGUMENTS)
        self._emit_event("magi_phase", {"phase": "opening_arguments"})
        positions = []

        roles = [
            ("eager", self.eager, MagiState.ROLE_EAGER),
            ("skeptic", self.skeptic, MagiState.ROLE_SKEPTIC),
            ("historian", self.historian, MagiState.ROLE_HISTORIAN),
        ]

        for role_name, role_agent, state in roles:
            self._check_cancel(f"before_magi_role:{role_name}:opening_arguments")
            self._set_state(state)
            self._emit_event("magi_role_start", {"role": role_name, "phase": "opening_arguments"})
            parsed = role_agent.opening_argument(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
                event_listener=self._make_role_streamer_listener(role_name, "opening_arguments"),
                evidence_pool_summary=evidence_pool_summary,
                enable_web_search=(role_name == "historian" and self._should_enable_historian_web_search(
                    phase="opening_arguments",
                    round_number=0,
                    unresolved_issue="",
                )),
            )
            position_text = parsed.get("position", "")
            self._emit_role_text(role_name, "opening_arguments", position_text)
            self._emit_event("magi_role_complete", {
                "role": role_name,
                "phase": "opening_arguments",
                "text": position_text,
                "position_length": len(position_text),
            })
            positions.append((role_name, parsed))
            self._check_cancel(f"after_magi_role:{role_name}:opening_arguments")

        return positions

    def _run_discussion(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions, evidence_pool_summary=""):
        return self._resume_discussion(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
            opening_positions,
            discussion_rounds=[],
            gate=None,
            start_round=1,
            start_role_index=0,
            interventions=[],
            emit_gate_event=True,
            evidence_pool_summary=evidence_pool_summary,
        )

    def _serialize_history(self, summarized_conversation_history):
        history = summarized_conversation_history
        if history is None:
            return {"summary_text": "", "recent_turns": []}
        return {
            "summary_text": str(getattr(history, "summary_text", "") or ""),
            "recent_turns": list(getattr(history, "recent_turns", []) or []),
        }

    def _serialize_positions(self, positions):
        serialized = []
        for role_name, parsed in positions or []:
            serialized.append({
                "role": role_name,
                "parsed": dict(parsed or {}),
            })
        return serialized

    def _deserialize_positions(self, serialized):
        positions = []
        for item in serialized or []:
            if not isinstance(item, dict):
                continue
            role_name = str(item.get("role") or "")
            parsed = dict(item.get("parsed") or {})
            if role_name:
                positions.append((role_name, parsed))
        return positions

    def _serialize_pause_state(
        self,
        *,
        user_query,
        retrieved_docs,
        summarized_conversation_history,
        memory_snapshot_text,
        opening_positions,
        discussion_rounds,
        gate,
        resume_checkpoint,
        interventions,
    ):
        return {
            "user_query": user_query,
            "retrieved_docs": retrieved_docs,
            "history": self._serialize_history(summarized_conversation_history),
            "memory_snapshot_text": memory_snapshot_text,
            "opening_positions": self._serialize_positions(opening_positions),
            "discussion_rounds": [self._serialize_positions(round_positions) for round_positions in discussion_rounds],
            "discussion_gate": dict(gate or {}),
            "resume_checkpoint": dict(resume_checkpoint or {}),
            "interventions": self._normalize_interventions(interventions),
        }

    def _resume_discussion(
        self,
        user_query,
        retrieved_docs,
        summarized_conversation_history,
        memory_snapshot_text,
        opening_positions,
        *,
        discussion_rounds,
        gate,
        start_round,
        start_role_index,
        interventions,
        emit_gate_event,
        evidence_pool_summary="",
    ):
        discussion_rounds = [list(round_positions or []) for round_positions in discussion_rounds or []]
        interventions = self._normalize_interventions(interventions)
        gate = dict(gate or self._discussion_gate(opening_positions))
        self._check_cancel("before_magi_discussion_gate")
        self._set_state(MagiState.DISCUSSION_GATE, gate)
        if emit_gate_event:
            self._emit_event("magi_discussion_gate", gate)
        if self.max_discussion_rounds <= 0 or not gate["force_discussion"]:
            return discussion_rounds

        for round_num in range(1, self.max_discussion_rounds + 1):
            if round_num < int(start_round or 1):
                continue
            while len(discussion_rounds) < round_num:
                discussion_rounds.append([])
            self._check_cancel(f"before_magi_discussion_round:{round_num}")
            unresolved_issue = self._determine_unresolved_issue(opening_positions, discussion_rounds)
            self._set_state(MagiState.DISCUSSION, {
                "round": round_num,
                "discussion_mode": gate["discussion_mode"],
                "unresolved_issue": unresolved_issue,
            })
            self._emit_event("magi_phase", {"phase": "discussion", "round": round_num})

            transcript = self._build_transcript(opening_positions, discussion_rounds, interventions=interventions)
            round_positions = discussion_rounds[round_num - 1]
            contributors = [role_name for role_name, parsed in round_positions if parsed.get("new_information")]
            role_start_index = int(start_role_index or 0) if round_num == int(start_round or 1) else len(round_positions)

            role_states = [
                ("eager", self.eager, MagiState.DISCUSSION_EAGER),
                ("skeptic", self.skeptic, MagiState.DISCUSSION_SKEPTIC),
                ("historian", self.historian, MagiState.DISCUSSION_HISTORIAN),
            ]

            for role_index, (role_name, role_agent, state) in enumerate(role_states):
                if role_index < role_start_index:
                    continue
                self._maybe_pause(
                    f"before_magi_role:{role_name}:discussion:{round_num}",
                    lambda round_num=round_num, role_index=role_index: self._serialize_pause_state(
                        user_query=user_query,
                        retrieved_docs=retrieved_docs,
                        summarized_conversation_history=summarized_conversation_history,
                        memory_snapshot_text=memory_snapshot_text,
                        opening_positions=opening_positions,
                        discussion_rounds=discussion_rounds,
                        gate=gate,
                        resume_checkpoint={
                            "round": round_num,
                            "after_role_count": len(round_positions),
                            "next_round": round_num,
                            "next_role_index": role_index,
                        },
                        interventions=interventions,
                    ),
                )
                self._check_cancel(f"before_magi_role:{role_name}:discussion:{round_num}")
                self._set_state(state, {"round": round_num})
                self._emit_event("magi_role_start", {"role": role_name, "phase": "discussion", "round": round_num})
                parsed = role_agent.discuss(
                    user_query,
                    retrieved_docs,
                    summarized_conversation_history,
                    memory_snapshot_text,
                    transcript,
                    round_num,
                    discussion_mode=gate["discussion_mode"],
                    unresolved_issue=unresolved_issue,
                    user_intervention_block=self._discussion_prompt_intervention_block(interventions),
                    event_listener=self._make_role_streamer_listener(role_name, "discussion", round_num),
                    evidence_pool_summary=evidence_pool_summary,
                    enable_web_search=(role_name == "historian" and self._should_enable_historian_web_search(
                        phase="discussion",
                        round_number=round_num,
                        unresolved_issue=unresolved_issue,
                    )),
                )
                position_text = parsed.get("position", "")
                new_info = parsed.get("new_information", False)
                self._emit_role_text(role_name, "discussion", position_text, round_num)
                self._emit_event("magi_role_complete", {
                    "role": role_name,
                    "phase": "discussion",
                    "round": round_num,
                    "discussion_mode": gate["discussion_mode"],
                    "text": position_text,
                    "new_information": new_info,
                    "no_delta_reason": parsed.get("no_delta_reason", ""),
                    "position_length": len(position_text),
                })
                round_positions.append((role_name, parsed))
                if new_info:
                    contributors.append(role_name)
                self._check_cancel(f"after_magi_role:{role_name}:discussion:{round_num}")
                discussion_rounds[round_num - 1] = round_positions
                self._maybe_pause(
                    f"after_magi_role:{role_name}:discussion:{round_num}",
                    lambda round_num=round_num, role_index=role_index: self._serialize_pause_state(
                        user_query=user_query,
                        retrieved_docs=retrieved_docs,
                        summarized_conversation_history=summarized_conversation_history,
                        memory_snapshot_text=memory_snapshot_text,
                        opening_positions=opening_positions,
                        discussion_rounds=discussion_rounds,
                        gate=gate,
                        resume_checkpoint={
                            "round": round_num,
                            "after_role_count": len(round_positions),
                            "next_round": round_num + 1 if (role_index + 1) >= len(role_states) else round_num,
                            "next_role_index": 0 if (role_index + 1) >= len(role_states) else (role_index + 1),
                        },
                        interventions=interventions,
                    ),
                )

            early_stop = len(contributors) == 0 and round_num >= 1
            self._emit_event("magi_discussion_round", {
                "round": round_num,
                "discussion_mode": gate["discussion_mode"],
                "unresolved_issue": unresolved_issue,
                "contributors": contributors,
                "early_stop": early_stop,
                "forced_round": gate["discussion_mode"] == "forced" and round_num == 1,
                "gate_reason": gate["reason"],
                "materially_divergent_openings": gate["materially_divergent_openings"],
                "grounding_strength": gate["grounding_strength"],
            })
            self._maybe_pause(
                f"after_magi_discussion_round:{round_num}",
                lambda round_num=round_num: self._serialize_pause_state(
                    user_query=user_query,
                    retrieved_docs=retrieved_docs,
                    summarized_conversation_history=summarized_conversation_history,
                    memory_snapshot_text=memory_snapshot_text,
                    opening_positions=opening_positions,
                    discussion_rounds=discussion_rounds,
                    gate=gate,
                    resume_checkpoint={
                        "round": round_num,
                        "after_role_count": len(round_positions),
                        "next_round": round_num + 1,
                        "next_role_index": 0,
                    },
                    interventions=interventions,
                ),
            )

            if early_stop:
                break

        return discussion_rounds

    def _run_closing_arguments(self, user_query, opening_positions, discussion_rounds):
        self._check_cancel("before_magi_closing_arguments")
        self._set_state(MagiState.CLOSING_ARGUMENTS)
        self._emit_event("magi_phase", {"phase": "closing_arguments"})
        closing_positions = []
        transcript = self._build_transcript(opening_positions, discussion_rounds)
        roles = [
            ("eager", self.eager, MagiState.CLOSING_EAGER),
            ("skeptic", self.skeptic, MagiState.CLOSING_SKEPTIC),
            ("historian", self.historian, MagiState.CLOSING_HISTORIAN),
        ]
        for role_name, role_agent, state in roles:
            self._check_cancel(f"before_magi_role:{role_name}:closing_arguments")
            self._set_state(state)
            self._emit_event("magi_role_start", {"role": role_name, "phase": "closing_arguments"})
            parsed = role_agent.closing_argument(
                user_query, transcript,
                event_listener=self._make_role_streamer_listener(role_name, "closing_arguments"),
            )
            position_text = parsed.get("position", "")
            self._emit_role_text(role_name, "closing_arguments", position_text)
            self._emit_event("magi_role_complete", {
                "role": role_name,
                "phase": "closing_arguments",
                "text": position_text,
                "position_length": len(position_text),
            })
            closing_positions.append((role_name, parsed))
            self._check_cancel(f"after_magi_role:{role_name}:closing_arguments")
        return closing_positions

    def _run_arbiter(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions, discussion_rounds, closing_positions=None, stream=False, interventions=None):
        self._check_cancel("before_magi_arbiter")
        self._set_state(MagiState.ARBITER)
        self._emit_event("magi_phase", {"phase": "arbiter"})

        transcript = self._build_transcript(opening_positions, discussion_rounds, closing_positions, interventions=interventions)

        if stream:
            arbiter_result = self.arbiter.synthesize_stream(
                user_query, retrieved_docs, summarized_conversation_history,
                memory_snapshot_text, transcript,
            )
        else:
            arbiter_result = self.arbiter.synthesize(
                user_query, retrieved_docs, summarized_conversation_history,
                memory_snapshot_text, transcript,
            )

        self.last_arbiter_metadata = {
            "primary_issue": arbiter_result.get("primary_issue", ""),
            "immediate_obligation": arbiter_result.get("immediate_obligation", ""),
            "decision_mode": arbiter_result.get("decision_mode", "best_current_branch"),
            "uncertainty_level": arbiter_result.get("uncertainty_level", "medium"),
            "winning_branch": arbiter_result.get("winning_branch", ""),
            "strongest_surviving_objection": arbiter_result.get("strongest_surviving_objection", ""),
            "missing_decisive_artifact": arbiter_result.get("missing_decisive_artifact", ""),
            "evidence_sources": list(arbiter_result.get("evidence_sources", []) or []),
        }
        response = arbiter_result.get("final_answer", "") or ""
        self._emit_event(
            "magi_synthesis_complete",
            {
                "response_length": len(response),
                **self.last_arbiter_metadata,
            },
        )
        self._set_state(MagiState.COMPLETE)
        self._check_cancel("after_magi_arbiter")
        return response

    def _build_council_entries(self, opening_positions, discussion_rounds, closing_positions=None, interventions=None):
        entries = []
        for role_name, parsed in opening_positions:
            entries.append({
                "entry_kind": "role",
                "role": role_name,
                "phase": "opening_arguments",
                "round": None,
                "text": parsed.get("position", ""),
            })
        interventions_by_round = self._interventions_by_round(interventions)
        for round_num, round_positions in enumerate(discussion_rounds, 1):
            pending_interventions = list(interventions_by_round.get(round_num, []))

            def _append_interventions(after_role_count):
                while pending_interventions and int(pending_interventions[0].get("after_role_count", 0) or 0) <= after_role_count:
                    intervention = pending_interventions.pop(0)
                    entries.append({
                        "entry_kind": "user_intervention",
                        "role": "user",
                        "phase": "discussion",
                        "round": round_num,
                        "input_kind": intervention.get("input_kind", "fact"),
                        "text": intervention.get("text", ""),
                    })

            _append_interventions(0)
            rendered_roles = 0
            for role_name, parsed in round_positions:
                position_text = parsed.get("position", "").strip()
                if position_text:
                    entries.append({
                        "entry_kind": "role",
                        "role": role_name,
                        "phase": "discussion",
                        "round": round_num,
                        "text": position_text,
                    })
                rendered_roles += 1
                _append_interventions(rendered_roles)
            _append_interventions(999)
        if closing_positions:
            for role_name, parsed in closing_positions:
                entries.append({
                    "entry_kind": "role",
                    "role": role_name,
                    "phase": "closing_arguments",
                    "round": None,
                    "text": parsed.get("position", ""),
                })
        return entries

    def _discussion_prompt_intervention_block(self, interventions):
        lines = []
        for entry in self._normalize_interventions(interventions):
            lines.append(f"- {entry.get('input_kind', 'fact')}: {entry.get('text', '')}")
        return "\n".join(lines) if lines else "none"

    def _resume_from_pause_state(self, pause_state):
        pause_state = dict(pause_state or {})
        return {
            "user_query": str(pause_state.get("user_query") or ""),
            "retrieved_docs": str(pause_state.get("retrieved_docs") or ""),
            "memory_snapshot_text": str(pause_state.get("memory_snapshot_text") or ""),
            "history": dict(pause_state.get("history") or {}),
            "opening_positions": self._deserialize_positions(pause_state.get("opening_positions") or []),
            "discussion_rounds": [
                self._deserialize_positions(round_positions)
                for round_positions in (pause_state.get("discussion_rounds") or [])
            ],
            "discussion_gate": dict(pause_state.get("discussion_gate") or {}),
            "resume_checkpoint": dict(pause_state.get("resume_checkpoint") or {}),
            "interventions": self._normalize_interventions(pause_state.get("interventions") or []),
        }

    def _run(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", *, stream=False, pause_state=None, evidence_pool_summary=""):
        self.last_arbiter_metadata = {}
        self.last_pause_state = {}
        interventions = []
        if pause_state:
            resumed = self._resume_from_pause_state(pause_state)
            user_query = resumed["user_query"] or user_query
            retrieved_docs = resumed["retrieved_docs"] or retrieved_docs
            memory_snapshot_text = resumed["memory_snapshot_text"] or memory_snapshot_text
            opening_positions = resumed["opening_positions"]
            discussion_rounds = resumed["discussion_rounds"]
            gate = resumed["discussion_gate"]
            checkpoint = resumed["resume_checkpoint"]
            interventions = resumed["interventions"]
            if hasattr(summarized_conversation_history, "summary_text"):
                history_summary = resumed["history"].get("summary_text", "")
                recent_turns = list(resumed["history"].get("recent_turns") or [])
                summarized_conversation_history.summary_text = history_summary
                summarized_conversation_history.recent_turns = recent_turns
            discussion_rounds = self._resume_discussion(
                user_query,
                retrieved_docs,
                summarized_conversation_history,
                memory_snapshot_text,
                opening_positions,
                discussion_rounds=discussion_rounds,
                gate=gate,
                start_round=int(checkpoint.get("next_round", 1) or 1),
                start_role_index=int(checkpoint.get("next_role_index", 0) or 0),
                interventions=interventions,
                emit_gate_event=False,
                evidence_pool_summary=evidence_pool_summary,
            )
        else:
            opening_positions = self._run_opening_arguments(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
                evidence_pool_summary=evidence_pool_summary,
            )
            discussion_rounds = self._run_discussion(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions,
                evidence_pool_summary=evidence_pool_summary,
            )
        closing_positions = self._run_closing_arguments(user_query, opening_positions, discussion_rounds)
        self.last_council_entries = self._build_council_entries(opening_positions, discussion_rounds, closing_positions, interventions=interventions)
        return self._run_arbiter(
            user_query,
            retrieved_docs,
            summarized_conversation_history,
            memory_snapshot_text,
            opening_positions,
            discussion_rounds,
            closing_positions=closing_positions,
            stream=stream,
            interventions=interventions,
        )

    def call_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", evidence_pool_summary=""):
        try:
            return self._run(
                user_query,
                retrieved_docs,
                summarized_conversation_history,
                memory_snapshot_text,
                stream=False,
                pause_state=None,
                evidence_pool_summary=evidence_pool_summary,
            )
        except RunPausedError:
            raise
        except Exception:
            self._set_state(MagiState.ERROR)
            raise

    def stream_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", evidence_pool_summary=""):
        try:
            return self._run(
                user_query,
                retrieved_docs,
                summarized_conversation_history,
                memory_snapshot_text,
                stream=True,
                pause_state=None,
                evidence_pool_summary=evidence_pool_summary,
            )
        except RunPausedError:
            raise
        except Exception:
            self._set_state(MagiState.ERROR)
            raise

    def resume_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text="", pause_state=None, stream=False, evidence_pool_summary=""):
        try:
            return self._run(
                user_query,
                retrieved_docs,
                summarized_conversation_history,
                memory_snapshot_text,
                stream=stream,
                pause_state=pause_state,
                evidence_pool_summary=evidence_pool_summary,
            )
        except RunPausedError:
            raise
        except Exception:
            self._set_state(MagiState.ERROR)
            raise
