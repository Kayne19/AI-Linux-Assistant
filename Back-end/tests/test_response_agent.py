"""ResponseAgent streaming regression tests."""

from agents.response_agent import ResponseAgent, ResponseState


class FakeStreamingWorker:
    def __init__(self, response_text="final answer", events=None):
        self.response_text = response_text
        self.events = list(events or [])
        self.calls = []

    def generate_text_stream(
        self,
        system_prompt="",
        user_message="",
        history=None,
        tools=None,
        tool_handler=None,
        max_tool_rounds=8,
        enable_web_search=False,
        event_listener=None,
        **kwargs,
    ):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "history": history,
                "tools": tools,
                "tool_handler": tool_handler,
                "max_tool_rounds": max_tool_rounds,
                "enable_web_search": enable_web_search,
                "kwargs": kwargs,
            }
        )
        for event_type, payload in self.events:
            if event_listener is not None:
                event_listener(event_type, payload)
        return self.response_text


def test_response_agent_stream_emits_final_visible_text_once():
    worker = FakeStreamingWorker(
        response_text="Final visible answer",
        events=[
            ("request_submitted", {"round": 0}),
            ("text_delta", {"delta": "F"}),
            ("text_delta", {"delta": "i"}),
            ("text_delta", {"delta": "n"}),
            ("response_completed", {"tool_rounds": 0}),
        ],
    )
    events = []

    agent = ResponseAgent(
        worker=worker,
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    response = agent.stream_api("question", "docs")

    assert response == "Final visible answer"
    text_deltas = [payload["delta"] for event_type, payload in events if event_type == "text_delta"]
    assert text_deltas == ["Final visible answer"]


def test_response_agent_stream_forwards_non_text_worker_events_and_states():
    worker = FakeStreamingWorker(
        response_text="final answer",
        events=[
            ("request_submitted", {"round": 0}),
            ("tool_calls_received", {"round": 1, "count": 1, "names": ["search_rag_database"]}),
            ("tool_results_submitted", {"round": 1}),
            ("response_completed", {"tool_rounds": 1}),
        ],
    )
    events = []
    states = []

    agent = ResponseAgent(
        worker=worker,
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
        state_listener=lambda state, payload: states.append((state, payload)),
    )

    agent.stream_api("question", "docs")

    assert ("request_submitted", {"round": 0}) in events
    assert ("tool_calls_received", {"round": 1, "count": 1, "names": ["search_rag_database"]}) in events
    assert ("tool_results_submitted", {"round": 1}) in events
    assert ("response_completed", {"tool_rounds": 1}) in events
    assert states[0][0] == ResponseState.PREPARE_REQUEST
    assert any(state == ResponseState.REQUEST_MODEL for state, _ in states)
    assert any(state == ResponseState.PROCESS_TOOL_CALLS for state, _ in states)
    assert any(state == ResponseState.SUBMIT_TOOL_RESULTS for state, _ in states)
    assert states[-1] == (ResponseState.COMPLETE, {"tool_rounds": 1})
