"""Worker-loop parity tests.

These tests do not call real providers. They use fake provider responses to
verify that Local and Anthropic follow the same repeated tool-round contract as
OpenAI: keep looping until tool calls stop, emit round-aware events, and return
the final model response.
"""

from types import SimpleNamespace

import providers.anthropic_caller as anthropic_module
import providers.local_caller as local_module
import providers.openAI_caller as openai_module


class FakeAnthropicBlock:
    def __init__(self, block_type, text=None, name=None, block_id=None, tool_input=None):
        self.type = block_type
        self.text = text
        self.name = name
        self.id = block_id
        self.input = tool_input


class FakeAnthropicResponse:
    def __init__(self, text="", tool_calls=None, stop_reason="end_turn", web_search_requests=0):
        self.content = []
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=web_search_requests))
        if text:
            self.content.append(FakeAnthropicBlock("text", text=text))
        for tool_call in tool_calls or []:
            self.content.append(
                FakeAnthropicBlock(
                    "tool_use",
                    name=tool_call["name"],
                    block_id=tool_call["id"],
                    tool_input=tool_call["input"],
                )
            )


class FakeAnthropicStreamEvent:
    def __init__(self, event_type, delta=None):
        self.type = event_type
        self.delta = delta


class FakeAnthropicStream:
    def __init__(self, response):
        self._response = response
        self._events = []
        for block in response.content:
            if block.type == "text" and block.text:
                self._events.append(
                    FakeAnthropicStreamEvent(
                        "content_block_delta",
                        delta=SimpleNamespace(text=block.text),
                    )
                )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._response


class FakeAnthropicMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeAnthropicStream(self.responses.pop(0))


class FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = FakeAnthropicMessages(responses)


class FakeOpenAIToolCall:
    def __init__(self, name, arguments, call_id):
        self.type = "function_call"
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class FakeOpenAIWebSearchCall:
    def __init__(self):
        self.type = "web_search_call"


class FakeOpenAIResponse:
    def __init__(self, output=None, output_text=""):
        self.output = output or []
        self.output_text = output_text
        self.id = "resp_123"


class FakeOpenAIResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.responses = FakeOpenAIResponses(responses)


def test_openai_worker_includes_native_web_search_and_emits_event():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = "low"
    worker.client = FakeOpenAIClient(
        [
            FakeOpenAIResponse(
                output=[FakeOpenAIWebSearchCall(), FakeOpenAIToolCall("lookup", "{\"q\":\"one\"}", "call_1")]
            ),
            FakeOpenAIResponse(output_text="final answer"),
        ]
    )

    tool_calls = []
    events = []

    def tool_handler(name, args):
        tool_calls.append((name, args))
        return {"ok": True}

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
        tool_handler=tool_handler,
        enable_web_search=True,
        event_listener=event_listener,
    )

    assert response == "final answer"
    assert tool_calls == [("lookup", {"q": "one"})]
    assert worker.client.responses.calls[0]["tools"][-1]["type"] == "web_search"
    assert ("web_search_used", {"provider": "openai", "count": 1, "round": 0}) in events


def test_openai_worker_normalizes_optional_tool_fields_for_strict_schemas():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = None

    translated = worker._translate_tools(
        [
            {
                "name": "search_rag_database",
                "description": "test",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "relevant_documents": {"type": "array", "items": {"type": "string"}},
                        "repeat_reason": {"type": "string", "enum": ["contradiction_check"]},
                        "requested_evidence_goal": {"type": "string"},
                    },
                    "required": ["query", "relevant_documents"],
                },
            }
        ]
    )

    schema = translated[0]["parameters"]
    assert translated[0]["strict"] is True
    assert schema["required"] == [
        "query",
        "relevant_documents",
        "repeat_reason",
        "requested_evidence_goal",
    ]
    assert schema["properties"]["query"] == {"type": "string"}
    assert schema["properties"]["repeat_reason"]["anyOf"][0] == {
        "type": "string",
        "enum": ["contradiction_check"],
    }
    assert schema["properties"]["repeat_reason"]["anyOf"][1] == {"type": "null"}
    assert schema["properties"]["requested_evidence_goal"]["anyOf"][1] == {"type": "null"}

def test_anthropic_worker_repeats_tool_rounds_until_completion():
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient(
        [
            FakeAnthropicResponse(tool_calls=[{"id": "toolu_1", "name": "lookup", "input": {"q": "one"}}]),
            FakeAnthropicResponse(tool_calls=[{"id": "toolu_2", "name": "lookup", "input": {"q": "two"}}]),
            FakeAnthropicResponse(text="final answer"),
        ]
    )

    tool_calls = []
    events = []

    def tool_handler(name, args):
        tool_calls.append((name, args))
        return {"ok": True}

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
        tool_handler=tool_handler,
        max_tool_rounds=4,
        event_listener=event_listener,
    )

    assert response == "final answer"
    assert tool_calls == [("lookup", {"q": "one"}), ("lookup", {"q": "two"})]
    assert len(worker.client.messages.calls) == 3
    assert events[-1] == ("response_completed", {"tool_rounds": 2})


def test_anthropic_worker_includes_native_web_search_and_emits_event():
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient(
        [
            FakeAnthropicResponse(text="web-backed answer", web_search_requests=1),
        ]
    )

    events = []

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[],
        enable_web_search=True,
        event_listener=event_listener,
    )

    assert response == "web-backed answer"
    assert worker.client.messages.calls[0]["tools"][-1]["type"] == "web_search_20250305"
    assert ("web_search_used", {"provider": "anthropic", "count": 1, "round": 0}) in events


def test_local_worker_repeats_tool_rounds_until_completion():
    original_chat = local_module.ollama.chat
    calls = []
    responses = [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "one"}}}]}},
        {"message": {"content": "", "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "two"}}}]}},
        {"message": {"content": "final answer", "tool_calls": []}},
    ]

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    local_module.ollama.chat = fake_chat
    try:
        worker = local_module.LocalCaller(model="fake-local")
        tool_calls = []
        events = []

        def tool_handler(name, args):
            tool_calls.append((name, args))
            return {"ok": True}

        def event_listener(event_type, payload):
            events.append((event_type, payload))

        response = worker.generate_text(
            system_prompt="sys",
            user_message="user",
            history=[],
            tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
            tool_handler=tool_handler,
            max_tool_rounds=4,
            event_listener=event_listener,
        )

        assert response == "final answer"
        assert tool_calls == [("lookup", {"q": "one"}), ("lookup", {"q": "two"})]
        assert len(calls) == 3
        assert events[-1] == ("response_completed", {"tool_rounds": 2})
    finally:
        local_module.ollama.chat = original_chat


def test_anthropic_worker_stream_emits_text_deltas():
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient([FakeAnthropicResponse(text="hello world")])

    events = []

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    result = worker.generate_text_stream(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[],
        event_listener=event_listener,
    )

    assert result == "hello world"
    delta_events = [e for e in events if e[0] == "text_delta"]
    assert len(delta_events) == 1
    assert delta_events[0][1]["provider"] == "anthropic"
    assert delta_events[0][1]["delta"] == "hello world"
    assert delta_events[0][1]["round"] == 0


def test_anthropic_worker_stream_handles_tool_rounds():
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient([
        FakeAnthropicResponse(tool_calls=[{"id": "toolu_1", "name": "lookup", "input": {"q": "one"}}]),
        FakeAnthropicResponse(text="streamed final answer"),
    ])

    tool_calls = []
    events = []

    def tool_handler(name, args):
        tool_calls.append((name, args))
        return {"ok": True}

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    result = worker.generate_text_stream(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
        tool_handler=tool_handler,
        max_tool_rounds=4,
        event_listener=event_listener,
    )

    assert result == "streamed final answer"
    assert tool_calls == [("lookup", {"q": "one"})]
    delta_events = [e for e in events if e[0] == "text_delta"]
    assert len(delta_events) == 1
    assert delta_events[0][1]["delta"] == "streamed final answer"
    assert delta_events[0][1]["round"] == 1
    assert events[-1] == ("response_completed", {"tool_rounds": 1})


def test_anthropic_worker_stream_handles_pause_turn():
    pause_response = FakeAnthropicResponse(text="part one", stop_reason="pause_turn")
    final_response = FakeAnthropicResponse(text="part two")
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient([pause_response, final_response])

    events = []

    def event_listener(event_type, payload):
        events.append((event_type, payload))

    result = worker.generate_text_stream(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[],
        event_listener=event_listener,
    )

    # The streaming path accumulates text from the pause round but only returns
    # the final round's text (matching the non-streaming _extract_text behaviour).
    assert result == "part two"
    delta_events = [e for e in events if e[0] == "text_delta"]
    assert len(delta_events) == 2
    assert delta_events[0][1]["delta"] == "part one"
    assert delta_events[1][1]["delta"] == "part two"
