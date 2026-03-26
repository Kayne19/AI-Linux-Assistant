"""Worker-loop parity tests.

These tests do not call real providers. They use fake Gemini/Ollama responses to
verify that Local and Gemini now follow the same repeated tool-round contract as
OpenAI: keep looping until tool calls stop, emit round-aware events, and return
the final model response.
"""

from types import SimpleNamespace

import gemini_caller as gemini_module
import local_caller as local_module


class FakeFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class FakeResponsePart:
    def __init__(self, function_call=None):
        self.function_call = function_call


class FakeCandidateContent:
    def __init__(self, parts):
        self.parts = parts


class FakeGeminiResponse:
    def __init__(self, text="", tool_calls=None):
        self.text = text
        parts = []
        for tool_call in tool_calls or []:
            parts.append(FakeResponsePart(function_call=FakeFunctionCall(tool_call["name"], tool_call["arguments"])))
        self.candidates = [SimpleNamespace(content=FakeCandidateContent(parts))]


class FakeGeminiModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeGeminiClient:
    def __init__(self, responses):
        self.models = FakeGeminiModels(responses)


class FakeTypes:
    class Content:
        def __init__(self, role, parts):
            self.role = role
            self.parts = parts

    class Part:
        def __init__(self, text=None, function_response=None):
            self.text = text
            self.function_response = function_response
            self.function_call = None

    class FunctionDeclaration:
        def __init__(self, name, description, parameters):
            self.name = name
            self.description = description
            self.parameters = parameters

    class Tool:
        def __init__(self, function_declarations):
            self.function_declarations = function_declarations

    class FunctionResponse:
        def __init__(self, name, response):
            self.name = name
            self.response = response

    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


def test_gemini_worker_repeats_tool_rounds_until_completion():
    original_types = gemini_module.types
    gemini_module.types = FakeTypes
    try:
        worker = gemini_module.GeminiCaller.__new__(gemini_module.GeminiCaller)
        worker.model = "fake-gemini"
        worker.client = FakeGeminiClient(
            [
                FakeGeminiResponse(tool_calls=[{"name": "lookup", "arguments": {"q": "one"}}]),
                FakeGeminiResponse(tool_calls=[{"name": "lookup", "arguments": {"q": "two"}}]),
                FakeGeminiResponse(text="final answer"),
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
    finally:
        gemini_module.types = original_types


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
