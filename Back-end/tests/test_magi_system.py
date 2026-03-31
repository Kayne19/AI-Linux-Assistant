"""Offline Magi system tests.

These tests stub all model-facing components to stay fast and deterministic.
They protect:
- deliberation protocol (opening → discussion → arbiter)
- event emission order and content
- discussion early-stop and bounded rounds
- tool access for all roles
- JSON parse fallback
- deliberation text visibility in events
"""

import json

from agents.magi.system import MagiSystem, MagiState
from agents.magi.roles import MagiEager, MagiSkeptic, MagiHistorian
from agents.magi.arbiter import MagiArbiter
from prompting.prompts import CHATBOT_SYSTEM_PROMPT
from prompting.magi_prompts import (
    MAGI_ARBITER_PROMPT,
    MAGI_EAGER_SYSTEM_PROMPT,
    MAGI_HISTORIAN_SYSTEM_PROMPT,
    MAGI_SKEPTIC_SYSTEM_PROMPT,
)


class FakeMagiWorker:
    def __init__(self, response_text=""):
        self.response_text = response_text
        self.calls = []

    def generate_text(self, system_prompt="", user_message="", history=None, tools=None,
                      tool_handler=None, max_tool_rounds=4, event_listener=None, **kwargs):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_message": user_message,
            "tools": tools,
            "tool_handler": tool_handler,
        })
        return self.response_text


def _make_role_response(position="test position", confidence="medium", key_claims=None, new_information=True):
    return json.dumps({
        "position": position,
        "confidence": confidence,
        "key_claims": key_claims or [],
        "new_information": new_information,
    })


def _build_magi(eager_text="", skeptic_text="", historian_text="", arbiter_text="final answer",
                max_discussion_rounds=1, tools=None, tool_handler=None):
    eager_worker = FakeMagiWorker(eager_text)
    skeptic_worker = FakeMagiWorker(skeptic_text)
    historian_worker = FakeMagiWorker(historian_text)
    arbiter_worker = FakeMagiWorker(arbiter_text)

    eager = MagiEager(worker=eager_worker, tools=tools, tool_handler=tool_handler)
    skeptic = MagiSkeptic(worker=skeptic_worker, tools=tools, tool_handler=tool_handler)
    historian = MagiHistorian(worker=historian_worker, tools=tools, tool_handler=tool_handler)
    arbiter = MagiArbiter(worker=arbiter_worker, tools=tools, tool_handler=tool_handler)

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
    )
    return system, events, states, {
        "eager_worker": eager_worker,
        "skeptic_worker": skeptic_worker,
        "historian_worker": historian_worker,
        "arbiter_worker": arbiter_worker,
    }


def test_magi_produces_response_from_all_roles():
    eager_resp = _make_role_response("disk is full", "high", ["df shows 100%"])
    skeptic_resp = _make_role_response("could be inode exhaustion", "medium", ["check df -i"])
    historian_resp = _make_role_response("user had this before on /var", "high", ["prior attempt: cleared logs"])
    system, events, states, workers = _build_magi(
        eager_text=eager_resp,
        skeptic_text=skeptic_resp,
        historian_text=historian_resp,
        arbiter_text="Check df -h and df -i to distinguish between space and inode exhaustion.",
    )

    response = system.call_api("disk is full", "some docs", memory_snapshot_text="OS: Debian 12")

    assert response == "Check df -h and df -i to distinguish between space and inode exhaustion."
    assert len(workers["eager_worker"].calls) >= 1
    assert len(workers["skeptic_worker"].calls) >= 1
    assert len(workers["historian_worker"].calls) >= 1
    assert len(workers["arbiter_worker"].calls) == 1


def test_magi_emits_phase_events_in_order():
    resp = _make_role_response("test", new_information=False)
    system, events, states, _ = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", max_discussion_rounds=1,
    )

    system.call_api("question", "")

    event_codes = [e[0] for e in events]
    assert event_codes[0] == "magi_phase"
    assert events[0][1]["phase"] == "opening_arguments"

    role_starts = [e for e in events if e[0] == "magi_role_start" and e[1].get("phase") == "opening_arguments"]
    assert [e[1]["role"] for e in role_starts] == ["eager", "skeptic", "historian"]

    assert "magi_synthesis_complete" in event_codes

    state_names = [s[0] for s in states]
    assert MagiState.OPENING_ARGUMENTS in state_names
    assert MagiState.ROLE_EAGER in state_names
    assert MagiState.ROLE_SKEPTIC in state_names
    assert MagiState.ROLE_HISTORIAN in state_names
    assert MagiState.ARBITER in state_names
    assert MagiState.COMPLETE in state_names


def test_magi_discussion_stops_early_when_no_new_info():
    opening_resp = _make_role_response("position")
    discussion_resp = _make_role_response("", new_information=False)
    system, events, states, workers = _build_magi(
        eager_text=opening_resp, skeptic_text=opening_resp, historian_text=opening_resp,
        arbiter_text="final", max_discussion_rounds=3,
    )
    # Override discussion responses to return no new info
    workers["eager_worker"].response_text = discussion_resp
    workers["skeptic_worker"].response_text = discussion_resp
    workers["historian_worker"].response_text = discussion_resp

    # But we need opening to return the opening response first.
    # Since workers return the same text for all calls, we need to handle this differently.
    # The opening call happens first with the opening_resp, then discussion calls happen.
    # But FakeMagiWorker returns the same text for all calls.
    # Solution: We override response_text after opening completes... but that's tricky with this setup.
    # Actually, the discussion_resp IS valid JSON with new_information=false, so it will work
    # for both opening (position is empty but parsed) and discussion. Let's use it directly.
    system2, events2, states2, workers2 = _build_magi(
        eager_text=discussion_resp, skeptic_text=discussion_resp, historian_text=discussion_resp,
        arbiter_text="final", max_discussion_rounds=3,
    )

    system2.call_api("question", "")

    round_events = [e for e in events2 if e[0] == "magi_discussion_round"]
    assert len(round_events) == 1
    assert round_events[0][1]["early_stop"] is True
    assert round_events[0][1]["contributors"] == []


def test_magi_discussion_bounded_by_max_rounds():
    resp = _make_role_response("still have more", new_information=True)
    system, events, states, _ = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", max_discussion_rounds=2,
    )

    system.call_api("question", "")

    round_events = [e for e in events if e[0] == "magi_discussion_round"]
    assert len(round_events) == 2
    assert round_events[0][1]["round"] == 1
    assert round_events[1][1]["round"] == 2


def test_magi_all_roles_receive_tools():
    tools = [{"name": "search_rag_database", "description": "test", "parameters": {}}]
    handler_calls = []

    def fake_handler(name, args):
        handler_calls.append((name, args))
        return "tool result"

    resp = _make_role_response("test", new_information=False)
    system, events, states, workers = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", tools=tools, tool_handler=fake_handler,
    )

    system.call_api("question", "")

    for role_key in ["eager_worker", "skeptic_worker", "historian_worker", "arbiter_worker"]:
        worker = workers[role_key]
        assert len(worker.calls) >= 1
        assert worker.calls[0]["tools"] == tools
        assert worker.calls[0]["tool_handler"] is not None


def test_magi_arbiter_uses_chatbot_prompt():
    resp = _make_role_response("test", new_information=False)
    system, events, states, workers = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final",
    )

    system.call_api("question", "")

    arbiter_call = workers["arbiter_worker"].calls[0]
    assert CHATBOT_SYSTEM_PROMPT in arbiter_call["system_prompt"]
    assert "ARBITER" in arbiter_call["system_prompt"]


def test_magi_role_json_parse_fallback():
    system, events, states, _ = _build_magi(
        eager_text="not valid json at all",
        skeptic_text="also not json",
        historian_text="nope",
        arbiter_text="final answer",
    )

    response = system.call_api("question", "")

    assert response == "final answer"


def test_magi_role_complete_events_include_text():
    eager_resp = _make_role_response("disk is probably full", "high")
    skeptic_resp = _make_role_response("check inodes too", "medium")
    historian_resp = _make_role_response("user cleared logs before", "high")
    system, events, states, _ = _build_magi(
        eager_text=eager_resp, skeptic_text=skeptic_resp, historian_text=historian_resp,
        arbiter_text="final", max_discussion_rounds=0,
    )

    system.call_api("question", "")

    complete_events = [e for e in events if e[0] == "magi_role_complete" and e[1].get("phase") == "opening_arguments"]
    assert len(complete_events) == 3

    eager_event = [e for e in complete_events if e[1]["role"] == "eager"][0]
    assert "disk is probably full" in eager_event[1]["text"]
    assert eager_event[1]["position_length"] > 0

    skeptic_event = [e for e in complete_events if e[1]["role"] == "skeptic"][0]
    assert "check inodes too" in skeptic_event[1]["text"]


def test_magi_discussion_rounds_receive_full_evidence_bundle_not_just_transcript():
    resp = _make_role_response("position", new_information=True)
    system, events, states, workers = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", max_discussion_rounds=1,
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


def test_magi_role_prompts_include_diagnostic_discipline():
    assert "Keep a small differential in mind" in MAGI_EAGER_SYSTEM_PROMPT
    assert "Prefer the next check that most clearly separates the leading branch" in MAGI_EAGER_SYSTEM_PROMPT
    assert "Attack premature closure" in MAGI_SKEPTIC_SYSTEM_PROMPT
    assert "project-environment mismatches" in MAGI_HISTORIAN_SYSTEM_PROMPT


def test_magi_prompts_allow_actionable_strategic_answers():
    assert "If the user is asking a strategic or design question" in MAGI_EAGER_SYSTEM_PROMPT
    assert "Do not bog the deliberation down with details that would not change the recommendation" in MAGI_SKEPTIC_SYSTEM_PROMPT
    assert "Do not inflate the answer with low-impact verification work" in MAGI_HISTORIAN_SYSTEM_PROMPT
    assert "default to an actionable recommendation under stated assumptions" in MAGI_ARBITER_PROMPT


def _make_closing_response(position="final position", confidence="high", key_claims=None):
    return json.dumps({
        "position": position,
        "confidence": confidence,
        "key_claims": key_claims or [],
    })


def test_magi_closing_arguments_always_runs():
    resp = _make_role_response("some position", new_information=False)
    system, events, states, workers = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", max_discussion_rounds=1,
    )

    system.call_api("question", "")

    # Each role is called at least twice: once for opening, once for closing
    assert len(workers["eager_worker"].calls) >= 2
    assert len(workers["skeptic_worker"].calls) >= 2
    assert len(workers["historian_worker"].calls) >= 2

    closing_starts = [e for e in events if e[0] == "magi_role_start" and e[1].get("phase") == "closing_arguments"]
    assert [e[1]["role"] for e in closing_starts] == ["eager", "skeptic", "historian"]


def test_magi_closing_args_emits_events_in_order():
    resp = _make_role_response("position", new_information=False)
    system, events, states, _ = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", max_discussion_rounds=1,
    )

    system.call_api("question", "")

    phase_events = [e for e in events if e[0] == "magi_phase"]
    phase_names = [e[1]["phase"] for e in phase_events]
    assert "closing_arguments" in phase_names

    closing_idx = phase_names.index("closing_arguments")
    arbiter_idx = phase_names.index("arbiter")
    assert closing_idx < arbiter_idx

    closing_starts = [e for e in events if e[0] == "magi_role_start" and e[1].get("phase") == "closing_arguments"]
    closing_completes = [e for e in events if e[0] == "magi_role_complete" and e[1].get("phase") == "closing_arguments"]
    assert len(closing_starts) == 3
    assert len(closing_completes) == 3

    state_names = [s[0] for s in states]
    assert MagiState.CLOSING_ARGUMENTS in state_names
    assert MagiState.CLOSING_EAGER in state_names
    assert MagiState.CLOSING_SKEPTIC in state_names
    assert MagiState.CLOSING_HISTORIAN in state_names


def test_magi_closing_args_included_in_arbiter_transcript():
    closing_text = _make_closing_response("my definitive conclusion", "high", ["claim A"])
    system, events, states, workers = _build_magi(
        eager_text=closing_text, skeptic_text=closing_text, historian_text=closing_text,
        arbiter_text="final", max_discussion_rounds=0,
    )

    system.call_api("question", "")

    arbiter_call = workers["arbiter_worker"].calls[0]
    assert "=== CLOSING ARGUMENTS ===" in arbiter_call["user_message"]
    assert "my definitive conclusion" in arbiter_call["user_message"]


def test_magi_closing_args_receive_no_tools():
    tools = [{"name": "search_rag_database", "description": "test", "parameters": {}}]

    def fake_handler(name, args):
        return "tool result"

    resp = _make_role_response("position", new_information=False)
    system, events, states, workers = _build_magi(
        eager_text=resp, skeptic_text=resp, historian_text=resp,
        arbiter_text="final", tools=tools, tool_handler=fake_handler, max_discussion_rounds=0,
    )

    system.call_api("question", "")

    # closing_argument always uses the last call per worker (after opening)
    for role_key in ["eager_worker", "skeptic_worker", "historian_worker"]:
        worker = workers[role_key]
        closing_call = worker.calls[-1]
        assert closing_call["tools"] == []
        assert closing_call["tool_handler"] is None


def test_magi_last_council_entries_populated_after_call():
    opening_resp = _make_role_response("opening position", new_information=True)
    closing_resp = _make_closing_response("closing position", "high")
    system, _, _, _ = _build_magi(
        eager_text=opening_resp, skeptic_text=opening_resp, historian_text=opening_resp,
        arbiter_text="final", max_discussion_rounds=1,
    )

    system.call_api("question", "")

    entries = system.last_council_entries
    assert isinstance(entries, list)
    assert len(entries) > 0

    phases = [e["phase"] for e in entries]
    assert "opening_arguments" in phases
    assert "closing_arguments" in phases

    roles_in_opening = [e["role"] for e in entries if e["phase"] == "opening_arguments"]
    assert set(roles_in_opening) == {"eager", "skeptic", "historian"}

    for entry in entries:
        assert "role" in entry
        assert "phase" in entry
        assert "round" in entry
        assert "text" in entry
