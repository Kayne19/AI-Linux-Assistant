"""Offline Magi system tests.

These tests stub all model-facing components to stay fast and deterministic.
They protect:
- deliberation protocol (opening -> discussion -> closing -> arbiter)
- role-aware parsing and fallback normalization
- visible council event emission
- forced discussion behavior
- arbiter metadata structure and hierarchy preservation
"""

import json

from agents.magi.arbiter import MagiArbiter
from agents.magi.roles import MagiEager, MagiHistorian, MagiSkeptic
from agents.magi.system import MagiState, MagiSystem
from orchestration.run_control import RunPausedError
from prompting.magi_prompts import (
    MAGI_ARBITER_PROMPT,
    MAGI_EAGER_SYSTEM_PROMPT,
    MAGI_HISTORIAN_SYSTEM_PROMPT,
    MAGI_SKEPTIC_SYSTEM_PROMPT,
)
from prompting.prompts import CHATBOT_SYSTEM_PROMPT


class FakeMagiWorker:
    def __init__(self, response_text=""):
        if isinstance(response_text, list):
            self.responses = list(response_text)
        else:
            self.responses = [response_text]
        self.calls = []
        self._call_index = 0

    def _next_response(self):
        if not self.responses:
            return ""
        index = min(self._call_index, len(self.responses) - 1)
        self._call_index += 1
        return self.responses[index]

    def generate_text(self, system_prompt="", user_message="", history=None, tools=None,
                      tool_handler=None, max_tool_rounds=4, event_listener=None, **kwargs):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "tools": tools,
            "tool_handler": tool_handler,
        })
        return self._next_response()

    def generate_text_stream(self, system_prompt="", user_message="", history=None, tools=None,
                             tool_handler=None, max_tool_rounds=4, event_listener=None, **kwargs):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "tools": tools,
            "tool_handler": tool_handler,
        })
        response = self._next_response()
        if event_listener is not None:
            for character in response:
                event_listener("text_delta", {"delta": character})
        return response


def _make_eager_response(
    position="test eager position",
    primary_issue=None,
    immediate_obligation="",
    provisional_branch="test branch",
    confidence="medium",
    key_claims=None,
    best_next_check="",
    strongest_caveat="",
    missing_decisive_artifact="",
    evidence_sources=None,
    new_information=None,
    no_delta_reason="",
):
    if primary_issue is None:
        primary_issue = provisional_branch
    payload = {
        "primary_issue": primary_issue,
        "immediate_obligation": immediate_obligation,
        "provisional_branch": provisional_branch,
        "position": position,
        "confidence": confidence,
        "key_claims": key_claims or [],
        "best_next_check": best_next_check,
        "strongest_caveat": strongest_caveat,
        "missing_decisive_artifact": missing_decisive_artifact,
        "evidence_sources": evidence_sources or [],
    }
    if new_information is not None:
        payload["new_information"] = new_information
        payload["no_delta_reason"] = no_delta_reason
    return json.dumps(payload)


def _make_skeptic_response(
    position="test skeptic objection",
    target_branch="test branch",
    confidence="medium",
    key_claims=None,
    weakest_assumption="",
    strongest_objection="",
    counterframe="",
    falsifying_check="",
    blocking_missing_artifact="",
    evidence_sources=None,
    new_information=None,
    no_delta_reason="",
):
    payload = {
        "target_branch": target_branch,
        "position": position,
        "confidence": confidence,
        "key_claims": key_claims or [],
        "weakest_assumption": weakest_assumption,
        "strongest_objection": strongest_objection,
        "counterframe": counterframe,
        "falsifying_check": falsifying_check,
        "blocking_missing_artifact": blocking_missing_artifact,
        "evidence_sources": evidence_sources or [],
    }
    if new_information is not None:
        payload["new_information"] = new_information
        payload["no_delta_reason"] = no_delta_reason
    return json.dumps(payload)


def _make_historian_response(
    position="historian grounding assessment",
    evaluated_branch="test branch",
    confidence="medium",
    grounding_strength="strong",
    branch_support_status="supports",
    memory_facts=None,
    doc_support=None,
    attempt_history=None,
    environment_fit="aligned",
    operator_warnings=None,
    most_relevant_evidence="",
    most_important_gap="",
    evidence_sources=None,
    new_information=None,
    no_delta_reason="",
):
    payload = {
        "evaluated_branch": evaluated_branch,
        "position": position,
        "confidence": confidence,
        "grounding_strength": grounding_strength,
        "branch_support_status": branch_support_status,
        "memory_facts": memory_facts or [],
        "doc_support": doc_support or [],
        "attempt_history": attempt_history or [],
        "environment_fit": environment_fit,
        "operator_warnings": operator_warnings or [],
        "most_relevant_evidence": most_relevant_evidence,
        "most_important_gap": most_important_gap,
        "evidence_sources": evidence_sources if evidence_sources is not None else (
            ["memory: known context"] if grounding_strength != "absent" else []
        ),
    }
    if new_information is not None:
        payload["new_information"] = new_information
        payload["no_delta_reason"] = no_delta_reason
    return json.dumps(payload)


def _make_eager_closing_response(
    position="final eager stance",
    provisional_branch="test branch",
    confidence="high",
    changed_since_opening=False,
    best_next_check="",
    strongest_caveat="",
    missing_decisive_artifact="",
):
    return json.dumps({
        "provisional_branch": provisional_branch,
        "position": position,
        "confidence": confidence,
        "changed_since_opening": changed_since_opening,
        "best_next_check": best_next_check,
        "strongest_caveat": strongest_caveat,
        "missing_decisive_artifact": missing_decisive_artifact,
    })


def _make_skeptic_closing_response(
    position="final skeptic stance",
    target_branch="test branch",
    confidence="high",
    changed_since_opening=False,
    strongest_objection="",
    falsifying_check="",
    blocking_missing_artifact="",
):
    return json.dumps({
        "target_branch": target_branch,
        "position": position,
        "confidence": confidence,
        "changed_since_opening": changed_since_opening,
        "strongest_objection": strongest_objection,
        "falsifying_check": falsifying_check,
        "blocking_missing_artifact": blocking_missing_artifact,
    })


def _make_historian_closing_response(
    position="final historian stance",
    evaluated_branch="test branch",
    confidence="high",
    changed_since_opening=False,
    grounding_strength="strong",
    branch_support_status="supports",
    most_relevant_evidence="",
    most_important_gap="",
):
    return json.dumps({
        "evaluated_branch": evaluated_branch,
        "position": position,
        "confidence": confidence,
        "changed_since_opening": changed_since_opening,
        "grounding_strength": grounding_strength,
        "branch_support_status": branch_support_status,
        "most_relevant_evidence": most_relevant_evidence,
        "most_important_gap": most_important_gap,
    })


def _make_arbiter_response(
    final_answer="final answer",
    primary_issue=None,
    immediate_obligation="",
    decision_mode="best_current_branch",
    uncertainty_level="medium",
    winning_branch="test branch",
    strongest_surviving_objection="",
    missing_decisive_artifact="",
    evidence_sources=None,
):
    if primary_issue is None:
        primary_issue = winning_branch
    return json.dumps({
        "primary_issue": primary_issue,
        "immediate_obligation": immediate_obligation,
        "decision_mode": decision_mode,
        "uncertainty_level": uncertainty_level,
        "winning_branch": winning_branch,
        "strongest_surviving_objection": strongest_surviving_objection,
        "missing_decisive_artifact": missing_decisive_artifact,
        "evidence_sources": evidence_sources or [],
        "final_answer": final_answer,
    })


def _build_magi(
    eager_text="",
    skeptic_text="",
    historian_text="",
    arbiter_text=None,
    max_discussion_rounds=1,
    tools=None,
    tool_handler=None,
    pause_check=None,
):
    eager_worker = FakeMagiWorker(eager_text)
    skeptic_worker = FakeMagiWorker(skeptic_text)
    historian_worker = FakeMagiWorker(historian_text)
    arbiter_worker = FakeMagiWorker(arbiter_text or _make_arbiter_response())

    eager = MagiEager(worker=eager_worker, tools=tools, tool_handler=tool_handler)
    skeptic = MagiSkeptic(worker=skeptic_worker, tools=tools, tool_handler=tool_handler)
    historian = MagiHistorian(worker=historian_worker, tools=tools, tool_handler=tool_handler)
    arbiter = MagiArbiter(worker=arbiter_worker, tools=[], tool_handler=None, max_tool_rounds=0)

    events = []
    states = []

    def on_state(state, payload):
        states.append((state, payload))

    def on_event(event_type, payload):
        events.append((event_type, payload))

    system = MagiSystem(
        eager=eager,
        skeptic=skeptic,
        historian=historian,
        arbiter=arbiter,
        max_discussion_rounds=max_discussion_rounds,
        state_listener=on_state,
        event_listener=on_event,
        pause_check=pause_check,
    )
    return system, events, states, {
        "eager_worker": eager_worker,
        "skeptic_worker": skeptic_worker,
        "historian_worker": historian_worker,
        "arbiter_worker": arbiter_worker,
    }


def test_magi_produces_response_from_all_roles():
    system, events, states, workers = _build_magi(
        eager_text=[
            _make_eager_response(
                position="Disk pressure points to a storage saturation issue.",
                provisional_branch="disk-vs-inode",
                key_claims=["df output should separate blocks from inodes"],
            ),
            _make_eager_closing_response(position="Still favor the disk-vs-inode branch."),
        ],
        skeptic_text=[
            _make_skeptic_response(
                position="The current branch is weak if inode exhaustion is not checked.",
                target_branch="disk-vs-inode",
                strongest_objection="The branch could miss inode exhaustion.",
                falsifying_check="Run df -i.",
            ),
            _make_skeptic_closing_response(position="The inode objection survives until df -i is checked."),
        ],
        historian_text=[
            _make_historian_response(
                position="Prior attempts mention clearing logs on /var.",
                evaluated_branch="disk-vs-inode",
                grounding_strength="strong",
                attempt_history=["prior attempt: cleared logs"],
                most_relevant_evidence="History shows a previous /var saturation event.",
            ),
            _make_historian_closing_response(position="History still supports checking disk and inode usage first."),
        ],
        arbiter_text=_make_arbiter_response(
            final_answer="Check df -h and df -i to distinguish between block exhaustion and inode exhaustion.",
            winning_branch="disk-vs-inode",
            evidence_sources=["history: prior attempt", "docs: df guidance"],
        ),
        max_discussion_rounds=0,
    )

    response = system.call_api("disk is full", "some docs", memory_snapshot_text="OS: Debian 12")

    assert response == "Check df -h and df -i to distinguish between block exhaustion and inode exhaustion."
    assert len(workers["eager_worker"].calls) >= 2
    assert len(workers["skeptic_worker"].calls) >= 2
    assert len(workers["historian_worker"].calls) >= 2
    assert len(workers["arbiter_worker"].calls) == 1


def test_magi_emits_phase_events_in_order():
    system, events, states, _ = _build_magi(
        eager_text=[_make_eager_response(), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final", decision_mode="consensus"),
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    event_codes = [event_type for event_type, _ in events]
    assert event_codes[0] == "magi_phase"
    assert events[0][1]["phase"] == "opening_arguments"
    assert "magi_synthesis_complete" in event_codes

    role_starts = [event for event in events if event[0] == "magi_role_start" and event[1].get("phase") == "opening_arguments"]
    assert [event[1]["role"] for event in role_starts] == ["eager", "skeptic", "historian"]

    state_names = [state for state, _ in states]
    assert MagiState.OPENING_ARGUMENTS in state_names
    assert MagiState.ROLE_EAGER in state_names
    assert MagiState.ROLE_SKEPTIC in state_names
    assert MagiState.ROLE_HISTORIAN in state_names
    assert MagiState.ARBITER in state_names
    assert MagiState.COMPLETE in state_names


def test_magi_discussion_stops_early_when_forced_roles_only_report_reasoned_no_delta():
    system, events, _, _ = _build_magi(
        eager_text=[
            _make_eager_response(provisional_branch="branch-a"),
            _make_eager_response(
                position="",
                provisional_branch="branch-a",
                new_information=False,
                no_delta_reason="blocked_by_missing_evidence",
            ),
            _make_eager_closing_response(),
        ],
        skeptic_text=[
            _make_skeptic_response(target_branch="branch-a"),
            _make_skeptic_response(
                position="",
                target_branch="branch-a",
                new_information=False,
                no_delta_reason="unresolved_issue_unchanged",
            ),
            _make_skeptic_closing_response(),
        ],
        historian_text=[
            _make_historian_response(
                evaluated_branch="branch-a",
                grounding_strength="weak",
                branch_support_status="weakens",
                doc_support=["docs are silent"],
                most_important_gap="Need the exact error output.",
            ),
            _make_historian_response(
                position="",
                evaluated_branch="branch-a",
                grounding_strength="weak",
                branch_support_status="weakens",
                new_information=False,
                no_delta_reason="no_grounding_change",
            ),
            _make_historian_closing_response(grounding_strength="weak", branch_support_status="weakens"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=3,
    )

    system.call_api("question", "")

    round_events = [payload for event_type, payload in events if event_type == "magi_discussion_round"]
    assert len(round_events) == 1
    assert round_events[0]["early_stop"] is True
    assert round_events[0]["contributors"] == []
    assert round_events[0]["discussion_mode"] == "forced"
    assert round_events[0]["unresolved_issue"]

    discussion_completes = [
        payload for event_type, payload in events
        if event_type == "magi_role_complete" and payload.get("phase") == "discussion"
    ]
    assert len(discussion_completes) == 3
    assert all(payload["text"] for payload in discussion_completes)
    assert all(payload["no_delta_reason"] for payload in discussion_completes)


def test_magi_discussion_bounded_by_max_rounds():
    system, events, _, _ = _build_magi(
        eager_text=[
            _make_eager_response(provisional_branch="branch-a"),
            _make_eager_response(
                position="Branch shifted after new signal.",
                provisional_branch="branch-a",
                new_information=True,
            ),
            _make_eager_response(
                position="Still a live delta.",
                provisional_branch="branch-a",
                new_information=True,
            ),
            _make_eager_closing_response(),
        ],
        skeptic_text=[
            _make_skeptic_response(target_branch="branch-a"),
            _make_skeptic_response(position="A sharper objection emerged.", target_branch="branch-a", new_information=True),
            _make_skeptic_response(position="Another falsifier matters.", target_branch="branch-a", new_information=True),
            _make_skeptic_closing_response(),
        ],
        historian_text=[
            _make_historian_response(grounding_strength="weak", branch_support_status="weakens"),
            _make_historian_response(position="Grounding improved slightly.", grounding_strength="weak", branch_support_status="weakens", new_information=True),
            _make_historian_response(position="Docs add one more fact.", grounding_strength="weak", branch_support_status="weakens", new_information=True),
            _make_historian_closing_response(grounding_strength="weak", branch_support_status="weakens"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=2,
    )

    system.call_api("question", "")

    round_events = [payload for event_type, payload in events if event_type == "magi_discussion_round"]
    assert len(round_events) == 2
    assert round_events[0]["round"] == 1
    assert round_events[1]["round"] == 2


def test_magi_skips_discussion_when_openings_align_and_grounding_is_strong():
    system, events, states, _ = _build_magi(
        eager_text=[_make_eager_response(provisional_branch="same branch"), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(target_branch="same branch"), _make_skeptic_closing_response()],
        historian_text=[
            _make_historian_response(
                evaluated_branch="same branch",
                grounding_strength="strong",
                branch_support_status="supports",
                memory_facts=["memory confirms branch"],
                doc_support=["docs confirm branch"],
            ),
            _make_historian_closing_response(),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=2,
    )

    system.call_api("question", "")

    gate_event = next(payload for event_type, payload in events if event_type == "magi_discussion_gate")
    round_events = [event for event in events if event[0] == "magi_discussion_round"]

    assert gate_event["force_discussion"] is False
    assert gate_event["discussion_mode"] == "optional"
    assert gate_event["grounding_strength"] == "strong"
    assert round_events == []
    assert MagiState.DISCUSSION_GATE in [state for state, _ in states]


def test_magi_forces_one_discussion_round_when_openings_diverge():
    system, events, _, _ = _build_magi(
        eager_text=[_make_eager_response(provisional_branch="branch eager"), _make_eager_closing_response()],
        skeptic_text=[
            _make_skeptic_response(
                target_branch="branch eager",
                counterframe="The framing may be a permissions boundary issue instead.",
            ),
            _make_skeptic_closing_response(),
        ],
        historian_text=[_make_historian_response(evaluated_branch="branch eager"), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=1,
    )

    system.call_api("question", "")

    gate_event = next(payload for event_type, payload in events if event_type == "magi_discussion_gate")
    round_events = [payload for event_type, payload in events if event_type == "magi_discussion_round"]

    assert gate_event["force_discussion"] is True
    assert gate_event["materially_divergent_openings"] is True
    assert len(round_events) == 1
    assert round_events[0]["forced_round"] is True
    assert round_events[0]["gate_reason"] == "material_opening_divergence"


def test_magi_forces_one_discussion_round_when_grounding_is_absent():
    system, events, _, _ = _build_magi(
        eager_text=[_make_eager_response(provisional_branch="same branch"), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(target_branch="same branch"), _make_skeptic_closing_response()],
        historian_text=[
            _make_historian_response(
                evaluated_branch="same branch",
                grounding_strength="absent",
                branch_support_status="absent",
                doc_support=["docs are silent"],
                most_important_gap="Need grounded evidence.",
            ),
            _make_historian_closing_response(grounding_strength="absent", branch_support_status="absent"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=1,
    )

    system.call_api("question", "")

    gate_event = next(payload for event_type, payload in events if event_type == "magi_discussion_gate")
    round_events = [payload for event_type, payload in events if event_type == "magi_discussion_round"]

    assert gate_event["force_discussion"] is True
    assert gate_event["grounding_strength"] == "absent"
    assert len(round_events) == 1
    assert round_events[0]["gate_reason"] == "grounding_absent"


def test_magi_can_pause_after_discussion_round_and_resume_with_user_intervention():
    system, events, _, _ = _build_magi(
        eager_text=[
            _make_eager_response(provisional_branch="branch-a"),
            _make_eager_response(position="Round 1 eager delta", provisional_branch="branch-a", new_information=True),
            _make_eager_response(position="Round 2 eager delta", provisional_branch="branch-a", new_information=True),
            _make_eager_closing_response(provisional_branch="branch-a"),
        ],
        skeptic_text=[
            _make_skeptic_response(target_branch="branch-a"),
            _make_skeptic_response(position="Round 1 skeptic delta", target_branch="branch-a", new_information=True),
            _make_skeptic_response(position="Round 2 skeptic delta", target_branch="branch-a", new_information=True),
            _make_skeptic_closing_response(target_branch="branch-a"),
        ],
        historian_text=[
            _make_historian_response(
                evaluated_branch="branch-a",
                grounding_strength="weak",
                branch_support_status="weakens",
                most_important_gap="Need one more fact.",
            ),
            _make_historian_response(
                position="Round 1 grounding delta",
                evaluated_branch="branch-a",
                grounding_strength="weak",
                branch_support_status="weakens",
                new_information=True,
            ),
            _make_historian_response(
                position="Round 2 grounding delta",
                evaluated_branch="branch-a",
                grounding_strength="weak",
                branch_support_status="weakens",
                new_information=True,
            ),
            _make_historian_closing_response(evaluated_branch="branch-a"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final answer"),
        max_discussion_rounds=2,
        pause_check=lambda checkpoint: checkpoint == "after_magi_discussion_round:1",
    )

    try:
        system.stream_api("question", "docs")
        assert False, "expected pause after round 1"
    except RunPausedError as exc:
        pause_state = dict(exc.pause_state)

    assert pause_state["resume_checkpoint"]["next_round"] == 2
    pause_state["interventions"] = [
        {
            "entry_kind": "user_intervention",
            "role": "user",
            "phase": "discussion",
            "round": 1,
            "after_role_count": 3,
            "input_kind": "fact",
            "text": "The server is Debian 12.",
        }
    ]
    system.pause_check = None

    response = system.resume_api("ignored", "", pause_state=pause_state, stream=False)

    assert response == "final answer"
    intervention_index = next(
        index for index, entry in enumerate(system.last_council_entries)
        if entry.get("entry_kind") == "user_intervention"
    )
    round_two_index = next(
        index for index, entry in enumerate(system.last_council_entries)
        if entry.get("phase") == "discussion" and entry.get("round") == 2
    )
    assert intervention_index < round_two_index
    assert any(event_type == "magi_discussion_round" and payload.get("round") == 1 for event_type, payload in events)


def test_magi_all_roles_receive_tools():
    tools = [{"name": "search_rag_database", "description": "test", "parameters": {}}]

    def fake_handler(name, args):
        return "tool result"

    system, _, _, workers = _build_magi(
        eager_text=[_make_eager_response(), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        tools=tools,
        tool_handler=fake_handler,
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    for role_key in ["eager_worker", "skeptic_worker", "historian_worker"]:
        worker = workers[role_key]
        assert len(worker.calls) >= 1
        assert worker.calls[0]["tools"] == tools
        assert worker.calls[0]["tool_handler"] is not None
    assert workers["arbiter_worker"].calls[0]["tools"] == []
    assert workers["arbiter_worker"].calls[0]["tool_handler"] is None


def test_magi_arbiter_uses_chatbot_prompt():
    system, _, _, workers = _build_magi(
        eager_text=[_make_eager_response(), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    arbiter_call = workers["arbiter_worker"].calls[0]
    assert CHATBOT_SYSTEM_PROMPT in arbiter_call["system_prompt"]
    assert "ARBITER" in arbiter_call["system_prompt"]


def test_magi_arbiter_metadata_is_required_internal_structure():
    system, events, _, _ = _build_magi(
        eager_text=[_make_eager_response(provisional_branch="branch"), _make_eager_closing_response(provisional_branch="branch")],
        skeptic_text=[_make_skeptic_response(target_branch="branch"), _make_skeptic_closing_response(target_branch="branch")],
        historian_text=[_make_historian_response(evaluated_branch="branch"), _make_historian_closing_response(evaluated_branch="branch")],
        arbiter_text=_make_arbiter_response(
            final_answer="final answer",
            primary_issue="real framing",
            immediate_obligation="stop changing config blindly",
            decision_mode="consensus",
            uncertainty_level="low",
            winning_branch="branch",
            evidence_sources=["docs: branch support"],
        ),
        max_discussion_rounds=0,
    )

    response = system.call_api("question", "")

    assert response == "real framing. final answer"
    assert system.last_arbiter_metadata["primary_issue"] == "real framing"
    assert system.last_arbiter_metadata["immediate_obligation"] == "stop changing config blindly"
    assert system.last_arbiter_metadata["decision_mode"] == "consensus"
    assert system.last_arbiter_metadata["uncertainty_level"] == "low"
    synthesis_event = next(payload for event_type, payload in events if event_type == "magi_synthesis_complete")
    assert synthesis_event["primary_issue"] == "real framing"
    assert synthesis_event["winning_branch"] == "branch"


def test_magi_historian_weak_absent_and_conflicted_grounding_are_valid_results():
    weak = MagiHistorian(worker=FakeMagiWorker(_make_historian_response(grounding_strength="weak", branch_support_status="weakens")))
    absent = MagiHistorian(worker=FakeMagiWorker(_make_historian_response(grounding_strength="absent", branch_support_status="absent")))
    conflicted = MagiHistorian(worker=FakeMagiWorker(_make_historian_response(grounding_strength="conflicted", branch_support_status="conflicted")))

    weak_result = weak.opening_argument("question", "", None, "")
    absent_result = absent.opening_argument("question", "", None, "")
    conflicted_result = conflicted.opening_argument("question", "", None, "")

    assert weak_result["grounding_strength"] == "weak"
    assert absent_result["grounding_strength"] == "absent"
    assert conflicted_result["grounding_strength"] == "conflicted"


def test_magi_role_json_parse_fallback():
    system, _, _, _ = _build_magi(
        eager_text=["not valid json at all", _make_eager_closing_response()],
        skeptic_text=["also not json", _make_skeptic_closing_response()],
        historian_text=["nope", _make_historian_closing_response()],
        arbiter_text="final answer",
        max_discussion_rounds=0,
    )

    response = system.call_api("question", "")

    assert response == "final answer"


def test_magi_role_complete_events_include_text():
    system, events, _, _ = _build_magi(
        eager_text=[_make_eager_response(position="disk is probably full"), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(position="check inodes too"), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(position="logs were cleared before"), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    complete_events = [payload for event_type, payload in events if event_type == "magi_role_complete" and payload.get("phase") == "opening_arguments"]
    assert len(complete_events) == 3
    eager_event = next(payload for payload in complete_events if payload["role"] == "eager")
    skeptic_event = next(payload for payload in complete_events if payload["role"] == "skeptic")
    assert "disk is probably full" in eager_event["text"]
    assert eager_event["position_length"] > 0
    assert "check inodes too" in skeptic_event["text"]


def test_magi_role_text_delta_events_emit_visible_position_text_only():
    system, events, _, _ = _build_magi(
        eager_text=[_make_eager_response(position="disk is probably full"), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(position="check inodes too"), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(position="logs were cleared before"), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=0,
    )

    system.stream_api("question", "")

    eager_opening_deltas = [
        payload["delta"]
        for event_type, payload in events
        if event_type == "magi_role_text_delta"
        and payload.get("role") == "eager"
        and payload.get("phase") == "opening_arguments"
    ]

    assert "".join(eager_opening_deltas) == "disk is probably full"
    assert eager_opening_deltas == ["disk is probably full"]
    assert not any('"position"' in delta for delta in eager_opening_deltas)
    assert not any("confidence" in delta for delta in eager_opening_deltas)


def test_magi_role_streamer_ignores_partial_provider_text_deltas():
    events = []
    system = MagiSystem(
        eager=None,
        skeptic=None,
        historian=None,
        arbiter=None,
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )
    listener = system._make_role_streamer_listener("eager", "opening_arguments")

    listener("request_submitted", {"round": 0})
    for char in '{"position":"draft claim","confidence":"low"}':
        listener("text_delta", {"delta": char})

    listener("request_submitted", {"round": 1})
    for char in '{"position":"final claim after tool check","confidence":"high"}':
        listener("text_delta", {"delta": char})

    delta_events = [payload for event_type, payload in events if event_type == "magi_role_text_delta"]
    assert delta_events == []


def test_magi_arbiter_stream_emits_final_text_once_after_stream_completion():
    worker = FakeMagiWorker(_make_arbiter_response(final_answer="final arbiter answer"))
    events = []
    arbiter = MagiArbiter(worker=worker, event_listener=lambda event_type, payload: events.append((event_type, payload)))

    response = arbiter.synthesize_stream("question", "docs", None, "", "transcript")

    assert response["final_answer"] == "final arbiter answer"
    text_deltas = [payload["delta"] for event_type, payload in events if event_type == "text_delta"]
    assert text_deltas == ["final arbiter answer"]


def test_magi_discussion_rounds_receive_full_evidence_bundle_not_just_transcript():
    system, _, _, workers = _build_magi(
        eager_text=[
            _make_eager_response(provisional_branch="host-package-conflict"),
            _make_eager_response(position="Need one more check.", provisional_branch="host-package-conflict", new_information=True),
            _make_eager_closing_response(),
        ],
        skeptic_text=[
            _make_skeptic_response(target_branch="host-package-conflict", strongest_objection="Maybe the environment assumption is wrong."),
            _make_skeptic_response(position="Still need the exact package state.", target_branch="host-package-conflict", new_information=True),
            _make_skeptic_closing_response(),
        ],
        historian_text=[
            _make_historian_response(
                evaluated_branch="host-package-conflict",
                grounding_strength="weak",
                branch_support_status="weakens",
                doc_support=["docs say proxmox host package guidance"],
                most_important_gap="Need exact apt error output.",
            ),
            _make_historian_response(
                position="Grounding unchanged.",
                evaluated_branch="host-package-conflict",
                grounding_strength="weak",
                branch_support_status="weakens",
                new_information=False,
                no_delta_reason="no_grounding_change",
            ),
            _make_historian_closing_response(grounding_strength="weak", branch_support_status="weakens"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=1,
    )

    system.call_api(
        "docker install broke my proxmox host",
        "docs say proxmox host package guidance",
        memory_snapshot_text="KNOWN SYSTEM PROFILE:\n- Platform: Proxmox",
    )

    eager_calls = workers["eager_worker"].calls
    assert len(eager_calls) >= 2
    discussion_call = eager_calls[1]
    assert "USER QUESTION:" in discussion_call["user_message"]
    assert "docker install broke my proxmox host" in discussion_call["user_message"]
    assert "KNOWN SYSTEM MEMORY:" in discussion_call["user_message"]
    assert "Platform: Proxmox" in discussion_call["user_message"]
    assert "REFERENCE CONTEXT:" in discussion_call["user_message"]
    assert "docs say proxmox host package guidance" in discussion_call["user_message"]
    assert "PRIOR TRANSCRIPT:" in discussion_call["user_message"]
    assert "DISCUSSION MODE:" in discussion_call["user_message"]
    assert "forced" in discussion_call["user_message"]
    assert "UNRESOLVED ISSUE TO ADVANCE:" in discussion_call["user_message"]


def test_magi_role_prompts_include_reasoning_discipline_and_high_ambiguity_mode():
    assert "Keep a small differential in mind" in MAGI_EAGER_SYSTEM_PROMPT
    assert "do NOT choose the winning branch".lower() in MAGI_SKEPTIC_SYSTEM_PROMPT.lower()
    assert "Treat weak, absent, or conflicted grounding as valid outcomes" in MAGI_HISTORIAN_SYSTEM_PROMPT
    assert "Judgment / interpersonal / high-ambiguity mode" in MAGI_EAGER_SYSTEM_PROMPT
    assert "preserve issue hierarchy" in MAGI_ARBITER_PROMPT.lower()


def test_magi_closing_arguments_always_runs():
    system, events, _, workers = _build_magi(
        eager_text=[_make_eager_response(), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=1,
    )

    system.call_api("question", "")

    assert len(workers["eager_worker"].calls) >= 2
    assert len(workers["skeptic_worker"].calls) >= 2
    assert len(workers["historian_worker"].calls) >= 2

    closing_starts = [payload for event_type, payload in events if event_type == "magi_role_start" and payload.get("phase") == "closing_arguments"]
    assert [payload["role"] for payload in closing_starts] == ["eager", "skeptic", "historian"]


def test_magi_closing_args_included_in_arbiter_transcript():
    system, _, _, workers = _build_magi(
        eager_text=[
            _make_eager_response(position="opening stance"),
            _make_eager_closing_response(position="my definitive eager conclusion"),
        ],
        skeptic_text=[
            _make_skeptic_response(position="opening objection"),
            _make_skeptic_closing_response(position="my definitive skeptic objection"),
        ],
        historian_text=[
            _make_historian_response(position="opening grounding"),
            _make_historian_closing_response(position="my definitive historian grounding"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    arbiter_call = workers["arbiter_worker"].calls[0]
    assert "=== CLOSING ARGUMENTS ===" in arbiter_call["user_message"]
    assert '"phase": "closing_arguments"' in arbiter_call["user_message"]
    assert "my definitive eager conclusion" in arbiter_call["user_message"]


def test_magi_closing_args_receive_no_tools():
    tools = [{"name": "search_rag_database", "description": "test", "parameters": {}}]

    def fake_handler(name, args):
        return "tool result"

    system, _, _, workers = _build_magi(
        eager_text=[_make_eager_response(), _make_eager_closing_response()],
        skeptic_text=[_make_skeptic_response(), _make_skeptic_closing_response()],
        historian_text=[_make_historian_response(), _make_historian_closing_response()],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        tools=tools,
        tool_handler=fake_handler,
        max_discussion_rounds=0,
    )

    system.call_api("question", "")

    for role_key in ["eager_worker", "skeptic_worker", "historian_worker"]:
        closing_call = workers[role_key].calls[-1]
        assert closing_call["tools"] == []
        assert closing_call["tool_handler"] is None


def test_magi_last_council_entries_populated_after_call():
    system, _, _, _ = _build_magi(
        eager_text=[
            _make_eager_response(position="opening position"),
            _make_eager_response(position="discussion delta", new_information=True),
            _make_eager_closing_response(position="closing position"),
        ],
        skeptic_text=[
            _make_skeptic_response(position="opening objection"),
            _make_skeptic_response(position="discussion objection", new_information=True),
            _make_skeptic_closing_response(position="closing objection"),
        ],
        historian_text=[
            _make_historian_response(position="opening grounding", grounding_strength="weak", branch_support_status="weakens"),
            _make_historian_response(position="discussion grounding", grounding_strength="weak", branch_support_status="weakens", new_information=True),
            _make_historian_closing_response(position="closing grounding", grounding_strength="weak", branch_support_status="weakens"),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=1,
    )

    system.call_api("question", "")

    entries = system.last_council_entries
    assert isinstance(entries, list)
    assert len(entries) > 0
    assert "opening_arguments" in [entry["phase"] for entry in entries]
    assert "closing_arguments" in [entry["phase"] for entry in entries]
    assert set(entry["role"] for entry in entries if entry["phase"] == "opening_arguments") == {"eager", "skeptic", "historian"}
    for entry in entries:
        assert "role" in entry
        assert "phase" in entry
        assert "round" in entry
        assert "text" in entry


def test_magi_skeptic_breach_fields_are_ignored_by_role_aware_parser():
    skeptic = MagiSkeptic(worker=FakeMagiWorker(json.dumps({
        "target_branch": "branch-a",
        "position": "The framing may be wrong.",
        "counterframe": "The real issue is a boundary problem.",
        "winning_branch": "branch-b",
        "final_answer": "Choose branch-b now.",
    })))

    result = skeptic.opening_argument("question", "", None, "")

    assert result["target_branch"] == "branch-a"
    assert result["counterframe"] == "The real issue is a boundary problem."
    assert "winning_branch" not in result
    assert "final_answer" not in result
    assert "provisional_branch" not in result


def test_magi_historian_breach_fields_are_ignored_by_role_aware_parser():
    historian = MagiHistorian(worker=FakeMagiWorker(json.dumps({
        "evaluated_branch": "branch-a",
        "position": "Docs are silent, so grounding is weak.",
        "grounding_strength": "weak",
        "branch_support_status": "weakens",
        "winning_branch": "branch-a",
        "final_answer": "Do branch-a anyway.",
    })))

    result = historian.opening_argument("question", "", None, "")

    assert result["evaluated_branch"] == "branch-a"
    assert result["grounding_strength"] == "weak"
    assert result["branch_support_status"] == "weakens"
    assert "winning_branch" not in result
    assert "final_answer" not in result
    assert "provisional_branch" not in result


def test_magi_arbiter_hierarchy_preserves_primary_issue_before_branch():
    arbiter = MagiArbiter(worker=FakeMagiWorker(_make_arbiter_response(
        primary_issue="The agreement boundary is unclear",
        immediate_obligation="Clarify the boundary before choosing a tactic",
        winning_branch="defer the tactical choice",
        final_answer="Use the best current branch once the tactical choice is clearer.",
    )))

    result = arbiter.synthesize("question", "docs", None, "", "transcript")

    assert result["primary_issue"] == "The agreement boundary is unclear"
    assert result["winning_branch"] == "defer the tactical choice"
    assert result["final_answer"].startswith("The agreement boundary is unclear")


def test_magi_judgment_mode_structure_survives_into_arbiter_transcript():
    system, _, _, workers = _build_magi(
        eager_text=[
            _make_eager_response(
                position="The framing is about trust, not just option ranking.",
                primary_issue="Trust and agreement boundaries are unclear",
                immediate_obligation="Clarify the boundary before choosing a tactic",
                provisional_branch="clarify-before-confronting",
            ),
            _make_eager_closing_response(provisional_branch="clarify-before-confronting"),
        ],
        skeptic_text=[
            _make_skeptic_response(
                target_branch="clarify-before-confronting",
                counterframe="The user may be optimizing a tactic before understanding the obligation.",
            ),
            _make_skeptic_closing_response(target_branch="clarify-before-confronting"),
        ],
        historian_text=[
            _make_historian_response(
                evaluated_branch="clarify-before-confronting",
                grounding_strength="absent",
                branch_support_status="absent",
                most_important_gap="There is no grounded context about the other person's expectations.",
            ),
            _make_historian_closing_response(
                evaluated_branch="clarify-before-confronting",
                grounding_strength="absent",
                branch_support_status="absent",
            ),
        ],
        arbiter_text=_make_arbiter_response(final_answer="final"),
        max_discussion_rounds=0,
    )

    system.call_api("Should I confront them now or clarify boundaries first?", "")

    arbiter_call = workers["arbiter_worker"].calls[0]
    assert '"primary_issue": "Trust and agreement boundaries are unclear"' in arbiter_call["user_message"]
    assert '"immediate_obligation": "Clarify the boundary before choosing a tactic"' in arbiter_call["user_message"]
    assert '"provisional_branch": "clarify-before-confronting"' in arbiter_call["user_message"]


def test_magi_closing_role_shape_preservation():
    eager = MagiEager(worker=FakeMagiWorker(_make_eager_closing_response(strongest_caveat="Evidence is still thin.")))
    skeptic = MagiSkeptic(worker=FakeMagiWorker(_make_skeptic_closing_response(strongest_objection="The causal claim is still unproven.", falsifying_check="Get the failing log line.")))
    historian = MagiHistorian(worker=FakeMagiWorker(_make_historian_closing_response(grounding_strength="weak", branch_support_status="weakens", most_relevant_evidence="Docs are silent.")))

    eager_result = eager.closing_argument("question", "transcript")
    skeptic_result = skeptic.closing_argument("question", "transcript")
    historian_result = historian.closing_argument("question", "transcript")

    assert eager_result["strongest_caveat"] == "Evidence is still thin."
    assert "falsifying_check" not in eager_result
    assert skeptic_result["falsifying_check"] == "Get the failing log line."
    assert historian_result["grounding_strength"] == "weak"
    assert historian_result["branch_support_status"] == "weakens"


def test_magi_parser_fallbacks_normalize_optional_and_required_fields():
    eager = MagiEager(worker=FakeMagiWorker(json.dumps({
        "position": "Minimal eager output",
        "provisional_branch": "branch-a",
    })))
    arbiter = MagiArbiter(worker=FakeMagiWorker(json.dumps({
        "final_answer": "Need more evidence.",
    })))

    eager_result = eager.opening_argument("question", "", None, "")
    arbiter_result = arbiter.synthesize("question", "docs", None, "", "transcript")

    assert eager_result["confidence"] == "medium"
    assert eager_result["best_next_check"] == ""
    assert eager_result["strongest_caveat"] == ""
    assert eager_result["missing_decisive_artifact"] == ""

    assert arbiter_result["primary_issue"] == "Need more evidence"
    assert arbiter_result["immediate_obligation"] == "Advance the best current branch without losing the higher-order framing."
    assert arbiter_result["decision_mode"] == "best_current_branch"
    assert arbiter_result["uncertainty_level"] == "medium"
