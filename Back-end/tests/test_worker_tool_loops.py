"""Worker-loop parity tests.

These tests do not call real providers. They use fake provider responses to
verify that Local and Anthropic follow the same repeated tool-round contract as
OpenAI: keep looping until tool calls stop, emit round-aware events, and return
the final model response.
"""

from types import SimpleNamespace

import providers.anthropic_caller as anthropic_module
import providers.google_caller as google_module
import providers.local_caller as local_module
import providers.openAI_caller as openai_module
from providers.step_protocol import ProviderToolCall


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
        self.errors = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self._responses.pop(0)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.responses = FakeOpenAIResponses(responses)


class FakeGoogleFunctionCall:
    def __init__(self, name, args, call_id):
        self.name = name
        self.args = args
        self.id = call_id


class FakeGoogleFunctionResponse:
    def __init__(self, name, response):
        self.name = name
        self.response = response


class FakeGooglePart:
    def __init__(self, text=None, function_call=None, function_response=None):
        self.text = text
        self.function_call = function_call
        self.function_response = function_response


class FakeGoogleContent:
    def __init__(self, role, parts):
        self.role = role
        self.parts = list(parts)


class FakeGoogleCandidate:
    def __init__(self, content):
        self.content = content


class FakeGoogleResponse:
    def __init__(self, text="", function_calls=None):
        parts = []
        if text:
            parts.append(FakeGooglePart(text=text))
        for function_call in function_calls or []:
            parts.append(FakeGooglePart(function_call=function_call))
        self.candidates = [FakeGoogleCandidate(FakeGoogleContent("model", parts))]
        self.function_calls = list(function_calls or [])
        self.text = text


class FakeGoogleChunk:
    def __init__(self, text=""):
        self.text = text


class FakeGoogleModels:
    def __init__(self, responses=None, stream_chunks=None):
        self.responses = list(responses or [])
        self.stream_chunks = list(stream_chunks or [])
        self.calls = []
        self.errors = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self.responses.pop(0)

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        chunks = self.stream_chunks.pop(0)
        return iter(chunks)


class FakeGoogleClient:
    def __init__(self, responses=None, stream_chunks=None):
        self.models = FakeGoogleModels(responses=responses, stream_chunks=stream_chunks)


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


def test_openai_worker_start_text_step_returns_tool_calls_without_internal_loop():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = "low"
    worker.client = FakeOpenAIClient(
        [
            FakeOpenAIResponse(
                output=[FakeOpenAIToolCall("lookup", "{\"q\":\"one\"}", "call_1")],
                output_text="",
            ),
            FakeOpenAIResponse(output_text="should not be consumed"),
        ]
    )

    events = []

    step = worker.start_text_step(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert [tool_call.name for tool_call in step.tool_calls] == ["lookup"]
    assert step.tool_calls[0] == ProviderToolCall(name="lookup", arguments={"q": "one"}, call_id="call_1")
    assert len(worker.client.responses.calls) == 1
    assert ("request_submitted", {"round": 0}) in events


def test_openai_worker_continue_text_step_submits_router_tool_results():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = "low"
    worker.client = FakeOpenAIClient(
        [
            FakeOpenAIResponse(output_text="final answer"),
        ]
    )

    step = worker.continue_text_step(
        system_prompt="sys",
        session_state={"response_id": "resp_prev"},
        tool_results=[{"call_id": "call_1", "name": "lookup", "arguments": {"q": "one"}, "output": "tool result"}],
        tools=[],
        enable_web_search=False,
    )

    assert step.output_text == "final answer"
    assert step.tool_calls == []
    assert worker.client.responses.calls[0]["previous_response_id"] == "resp_prev"
    assert worker.client.responses.calls[0]["input"] == [
        {"type": "function_call_output", "call_id": "call_1", "output": "tool result"}
    ]


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
                        "evidence_gap": {"type": "string"},
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
        "evidence_gap",
    ]
    assert schema["properties"]["query"] == {"type": "string"}
    assert schema["properties"]["repeat_reason"]["anyOf"][0] == {
        "type": "string",
        "enum": ["contradiction_check"],
    }
    assert schema["properties"]["repeat_reason"]["anyOf"][1] == {"type": "null"}
    assert schema["properties"]["evidence_gap"]["anyOf"][1] == {"type": "null"}


def test_openai_worker_requests_native_structured_output_when_schema_is_provided():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = None
    worker.client = FakeOpenAIClient([FakeOpenAIResponse(output_text='{"answer":"ok"}')])

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        structured_output=True,
        output_schema={
            "title": "role_output",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "answer": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["answer"],
        },
    )

    assert response == '{"answer":"ok"}'
    request = worker.client.responses.calls[0]
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["name"] == "role_output"
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["required"] == ["answer", "note"]


def test_openai_worker_warns_and_falls_back_when_native_structured_output_fails():
    worker = openai_module.OpenAICaller.__new__(openai_module.OpenAICaller)
    worker.model = "fake-openai"
    worker.reasoning_effort = None
    worker.client = FakeOpenAIClient([FakeOpenAIResponse(output_text='{"answer":"fallback"}')])
    worker.client.responses.errors.append(RuntimeError("structured outputs unsupported"))
    events = []

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        structured_output=True,
        output_schema={
            "title": "role_output",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert response == '{"answer":"fallback"}'
    assert len(worker.client.responses.calls) == 2
    assert "text" in worker.client.responses.calls[0]
    assert "text" not in worker.client.responses.calls[1]
    assert (
        "structured_output_warning",
        {
            "provider": "openai",
            "model": "fake-openai",
            "schema_name": "role_output",
            "reason": "structured outputs unsupported",
            "native_method": "responses.text.format",
            "used_prompt_fallback": True,
        },
    ) in events


def test_google_worker_repeats_tool_rounds_until_completion():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(
        responses=[
            FakeGoogleResponse(function_calls=[FakeGoogleFunctionCall("lookup", {"q": "one"}, "call_1")]),
            FakeGoogleResponse(function_calls=[FakeGoogleFunctionCall("lookup", {"q": "two"}, "call_2")]),
            FakeGoogleResponse(text="final answer"),
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
    assert len(worker.client.models.calls) == 3
    assert events[-1] == ("response_completed", {"tool_rounds": 2})


def test_google_worker_start_text_step_returns_tool_calls_without_internal_loop():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(
        responses=[
            FakeGoogleResponse(function_calls=[FakeGoogleFunctionCall("lookup", {"q": "one"}, "call_1")]),
            FakeGoogleResponse(text="should not be consumed"),
        ]
    )

    events = []

    step = worker.start_text_step(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[{"name": "lookup", "description": "test", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert [tool_call.name for tool_call in step.tool_calls] == ["lookup"]
    assert step.tool_calls[0] == ProviderToolCall(name="lookup", arguments={"q": "one"}, call_id="call_1")
    assert len(worker.client.models.calls) == 1
    assert ("request_submitted", {"round": 0}) in events


def test_google_worker_continue_text_step_submits_router_tool_results():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(
        responses=[
            FakeGoogleResponse(text="final answer"),
        ]
    )

    step = worker.continue_text_step(
        system_prompt="sys",
        session_state={
            "contents": [
                {"role": "user", "parts": [{"text": "user"}]},
                {"role": "model", "parts": [{"function_call": {"name": "lookup", "args": {"q": "one"}, "id": "call_1"}}]},
            ]
        },
        tool_results=[{"call_id": "call_1", "name": "lookup", "arguments": {"q": "one"}, "output": "tool result"}],
        tools=[],
        enable_web_search=False,
    )

    assert step.output_text == "final answer"
    assert step.tool_calls == []
    request_contents = worker.client.models.calls[0]["contents"]
    assert request_contents[-1] == {
        "role": "tool",
        "parts": [
            {
                "function_response": {
                    "name": "lookup",
                    "response": {"output": "tool result"},
                }
            }
        ],
    }


def test_google_worker_requests_native_structured_output_when_schema_is_provided():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(responses=[FakeGoogleResponse(text='{"answer":"ok"}')])

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        structured_output=True,
        output_schema={
            "title": "role_output",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )

    assert response == '{"answer":"ok"}'
    request = worker.client.models.calls[0]
    assert request["config"]["response_mime_type"] == "application/json"
    assert request["config"]["response_schema"]["title"] == "role_output"


def test_google_worker_warns_and_falls_back_when_native_structured_output_fails():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(responses=[FakeGoogleResponse(text='{"answer":"fallback"}')])
    worker.client.models.errors.append(RuntimeError("structured outputs unsupported"))
    events = []

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        structured_output=True,
        output_schema={
            "title": "role_output",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert response == '{"answer":"fallback"}'
    assert len(worker.client.models.calls) == 2
    assert "response_schema" in worker.client.models.calls[0]["config"]
    assert "response_schema" not in worker.client.models.calls[1]["config"]
    assert (
        "structured_output_warning",
        {
            "provider": "google",
            "model": "fake-gemini",
            "schema_name": "role_output",
            "reason": "structured outputs unsupported",
            "native_method": "generate_content.config.response_schema",
            "used_prompt_fallback": True,
        },
    ) in events


def test_google_worker_stream_emits_text_deltas():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(
        stream_chunks=[
            [
                FakeGoogleChunk(text="hello "),
                FakeGoogleChunk(text="world"),
            ]
        ]
    )

    events = []

    result = worker.generate_text_stream(
        system_prompt="sys",
        user_message="user",
        history=[],
        tools=[],
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert result == "hello world"
    delta_events = [event for event in events if event[0] == "text_delta"]
    assert delta_events == [
        ("text_delta", {"provider": "google", "round": 0, "delta": "hello "}),
        ("text_delta", {"provider": "google", "round": 0, "delta": "world"}),
    ]


def test_google_worker_emits_explicit_event_when_native_web_search_is_requested():
    worker = google_module.GoogleCaller.__new__(google_module.GoogleCaller)
    worker.model = "fake-gemini"
    worker.client = FakeGoogleClient(responses=[FakeGoogleResponse(text="answer")])
    events = []

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        enable_web_search=True,
        event_listener=lambda event_type, payload: events.append((event_type, payload)),
    )

    assert response == "answer"
    assert ("web_search_unsupported", {"provider": "google", "round": 0}) in events


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


def test_anthropic_worker_requests_native_structured_output_when_schema_is_provided():
    worker = anthropic_module.AnthropicCaller.__new__(anthropic_module.AnthropicCaller)
    worker.model = "fake-claude"
    worker.client = FakeAnthropicClient([FakeAnthropicResponse(text='{"answer":"ok"}')])

    response = worker.generate_text(
        system_prompt="sys",
        user_message="user",
        history=[],
        structured_output=True,
        output_schema={
            "title": "role_output",
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )

    assert response == '{"answer":"ok"}'
    request = worker.client.messages.calls[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["format"]["schema"]["title"] == "role_output"
    assert request["output_config"]["format"]["schema"]["required"] == ["answer"]


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


def test_local_worker_warns_and_falls_back_for_structured_output_requests():
    original_chat = local_module.ollama.chat
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"message": {"content": '{"answer":"local"}', "tool_calls": []}}

    local_module.ollama.chat = fake_chat
    try:
        worker = local_module.LocalCaller(model="fake-local")
        events = []

        response = worker.generate_text(
            system_prompt="sys",
            user_message="user",
            history=[],
            structured_output=True,
            output_schema={
                "title": "role_output",
                "type": "object",
                "additionalProperties": False,
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
            event_listener=lambda event_type, payload: events.append((event_type, payload)),
        )

        assert response == '{"answer":"local"}'
        assert len(calls) == 1
        assert "format" not in calls[0]
        assert (
            "structured_output_warning",
            {
                "provider": "local",
                "model": "fake-local",
                "schema_name": "role_output",
                "reason": "native structured output unsupported",
                "native_method": "none",
                "used_prompt_fallback": True,
            },
        ) in events
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
