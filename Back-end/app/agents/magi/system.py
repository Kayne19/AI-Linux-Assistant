import json
from enum import Enum, auto

from orchestration.run_control import invoke_cancel_check


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
        """Returns an event listener that forwards non-text provider events with role/phase context kept external."""

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

    def _format_position(self, role_name, parsed):
        branch = parsed.get("branch", "")
        position = parsed.get("position", "")
        confidence = parsed.get("confidence", "?")
        claims = parsed.get("key_claims", [])
        best_next_check = parsed.get("best_next_check", "")
        strongest_objection = parsed.get("strongest_objection", "")
        missing_decisive_artifact = parsed.get("missing_decisive_artifact", "")
        missing_evidence = parsed.get("missing_evidence", [])
        evidence_sources = parsed.get("evidence_sources", [])
        lines = [f"[{role_name.upper()}] (confidence: {confidence})"]
        if branch:
            lines.append(f"Branch: {branch}")
        lines.append(position)
        if claims:
            lines.append("Key claims: " + "; ".join(claims))
        if best_next_check:
            lines.append("Best next check: " + best_next_check)
        if strongest_objection:
            lines.append("Strongest objection: " + strongest_objection)
        if missing_decisive_artifact:
            lines.append("Missing decisive artifact: " + missing_decisive_artifact)
        if missing_evidence:
            lines.append("Missing evidence: " + "; ".join(item for item in missing_evidence if item))
        if evidence_sources:
            lines.append("Evidence sources: " + "; ".join(item for item in evidence_sources if item))
        if role_name == "historian":
            grounding_strength = parsed.get("grounding_strength", "")
            memory_facts = parsed.get("memory_facts", [])
            doc_support = parsed.get("doc_support", [])
            attempt_history = parsed.get("attempt_history", [])
            environment_fit = parsed.get("environment_fit", "")
            operator_warnings = parsed.get("operator_warnings", [])
            if grounding_strength:
                lines.append("Grounding strength: " + grounding_strength)
            if memory_facts:
                lines.append("Memory facts: " + "; ".join(item for item in memory_facts if item))
            if doc_support:
                lines.append("Doc support: " + "; ".join(item for item in doc_support if item))
            if attempt_history:
                lines.append("Attempt history: " + "; ".join(item for item in attempt_history if item))
            if environment_fit:
                lines.append("Environment fit: " + environment_fit)
            if operator_warnings:
                lines.append("Operator warnings: " + "; ".join(item for item in operator_warnings if item))
        return "\n".join(lines)

    def _find_role_payload(self, positions, role_name):
        for candidate_role, parsed in positions:
            if candidate_role == role_name:
                return parsed
        return {}

    def _normalize_branch_key(self, value):
        return " ".join(str(value or "").strip().lower().split())

    def _openings_materially_diverge(self, opening_positions):
        branches = {
            self._normalize_branch_key(parsed.get("branch", ""))
            for _, parsed in opening_positions
            if self._normalize_branch_key(parsed.get("branch", ""))
        }
        if len(branches) > 1:
            return True

        next_checks = {
            self._normalize_branch_key(parsed.get("best_next_check", ""))
            for _, parsed in opening_positions
            if self._normalize_branch_key(parsed.get("best_next_check", ""))
        }
        return len(next_checks) > 1

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
            "materially_divergent_openings": materially_divergent,
            "grounding_strength": grounding_strength,
            "reason": reason,
        }

    def _build_transcript(self, opening_positions, discussion_rounds, closing_positions=None):
        sections = ["=== OPENING ARGUMENTS ==="]
        for role_name, parsed in opening_positions:
            sections.append(self._truncate(self._format_position(role_name, parsed)))
            sections.append("")

        for round_num, round_positions in enumerate(discussion_rounds, 1):
            sections.append(f"=== DISCUSSION ROUND {round_num} ===")
            for role_name, parsed in round_positions:
                position_text = parsed.get("position", "").strip()
                if position_text:
                    sections.append(self._truncate(self._format_position(role_name, parsed)))
                    sections.append("")

        if closing_positions:
            sections.append("=== CLOSING ARGUMENTS ===")
            for role_name, parsed in closing_positions:
                sections.append(self._truncate(self._format_position(role_name, parsed)))
                sections.append("")

        return "\n".join(sections).strip()

    def _run_opening_arguments(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text):
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

    def _run_discussion(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions):
        discussion_rounds = []
        gate = self._discussion_gate(opening_positions)
        self._check_cancel("before_magi_discussion_gate")
        self._set_state(MagiState.DISCUSSION_GATE, gate)
        self._emit_event("magi_discussion_gate", gate)
        if self.max_discussion_rounds <= 0 or not gate["force_discussion"]:
            return discussion_rounds

        for round_num in range(1, self.max_discussion_rounds + 1):
            self._check_cancel(f"before_magi_discussion_round:{round_num}")
            self._set_state(MagiState.DISCUSSION, {"round": round_num})
            self._emit_event("magi_phase", {"phase": "discussion", "round": round_num})

            transcript = self._build_transcript(opening_positions, discussion_rounds)
            round_positions = []
            contributors = []

            role_states = [
                ("eager", self.eager, MagiState.DISCUSSION_EAGER),
                ("skeptic", self.skeptic, MagiState.DISCUSSION_SKEPTIC),
                ("historian", self.historian, MagiState.DISCUSSION_HISTORIAN),
            ]

            for role_name, role_agent, state in role_states:
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
                    event_listener=self._make_role_streamer_listener(role_name, "discussion", round_num),
                )
                position_text = parsed.get("position", "")
                new_info = parsed.get("new_information", False)
                self._emit_role_text(role_name, "discussion", position_text, round_num)
                self._emit_event("magi_role_complete", {
                    "role": role_name,
                    "phase": "discussion",
                    "round": round_num,
                    "text": position_text,
                    "new_information": new_info,
                    "position_length": len(position_text),
                })
                round_positions.append((role_name, parsed))
                if new_info:
                    contributors.append(role_name)
                self._check_cancel(f"after_magi_role:{role_name}:discussion:{round_num}")

            discussion_rounds.append(round_positions)
            early_stop = len(contributors) == 0 and round_num >= 1
            self._emit_event("magi_discussion_round", {
                "round": round_num,
                "contributors": contributors,
                "early_stop": early_stop,
                "forced_round": round_num == 1,
                "gate_reason": gate["reason"],
                "materially_divergent_openings": gate["materially_divergent_openings"],
                "grounding_strength": gate["grounding_strength"],
            })

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

    def _run_arbiter(self, user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions, discussion_rounds, closing_positions=None, stream=False):
        self._check_cancel("before_magi_arbiter")
        self._set_state(MagiState.ARBITER)
        self._emit_event("magi_phase", {"phase": "arbiter"})

        transcript = self._build_transcript(opening_positions, discussion_rounds, closing_positions)

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

    def _build_council_entries(self, opening_positions, discussion_rounds, closing_positions=None):
        entries = []
        for role_name, parsed in opening_positions:
            entries.append({
                "role": role_name,
                "phase": "opening_arguments",
                "round": None,
                "text": parsed.get("position", ""),
            })
        for round_num, round_positions in enumerate(discussion_rounds, 1):
            for role_name, parsed in round_positions:
                position_text = parsed.get("position", "").strip()
                if position_text:
                    entries.append({
                        "role": role_name,
                        "phase": "discussion",
                        "round": round_num,
                        "text": position_text,
                    })
        if closing_positions:
            for role_name, parsed in closing_positions:
                entries.append({
                    "role": role_name,
                    "phase": "closing_arguments",
                    "round": None,
                    "text": parsed.get("position", ""),
                })
        return entries

    def call_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text=""):
        try:
            self.last_arbiter_metadata = {}
            opening_positions = self._run_opening_arguments(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
            )
            discussion_rounds = self._run_discussion(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions,
            )
            closing_positions = self._run_closing_arguments(user_query, opening_positions, discussion_rounds)
            self.last_council_entries = self._build_council_entries(opening_positions, discussion_rounds, closing_positions)
            response = self._run_arbiter(
                user_query, retrieved_docs, summarized_conversation_history,
                memory_snapshot_text, opening_positions, discussion_rounds,
                closing_positions=closing_positions,
                stream=False,
            )
            return response
        except Exception:
            self._set_state(MagiState.ERROR)
            raise

    def stream_api(self, user_query, retrieved_docs, summarized_conversation_history=None, memory_snapshot_text=""):
        try:
            self.last_arbiter_metadata = {}
            opening_positions = self._run_opening_arguments(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text,
            )
            discussion_rounds = self._run_discussion(
                user_query, retrieved_docs, summarized_conversation_history, memory_snapshot_text, opening_positions,
            )
            closing_positions = self._run_closing_arguments(user_query, opening_positions, discussion_rounds)
            self.last_council_entries = self._build_council_entries(opening_positions, discussion_rounds, closing_positions)
            response = self._run_arbiter(
                user_query, retrieved_docs, summarized_conversation_history,
                memory_snapshot_text, opening_positions, discussion_rounds,
                closing_positions=closing_positions,
                stream=True,
            )
            return response
        except Exception:
            self._set_state(MagiState.ERROR)
            raise
