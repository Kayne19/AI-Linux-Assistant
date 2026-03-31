import importlib
import json
import os

from orchestration.run_control import invoke_cancel_check

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    def load_dotenv():
        return False


class AnthropicCaller:
    def __init__(self, model="claude-sonnet-4-6"):
        load_dotenv()
        self.API_KEY = os.getenv("ANTHROPIC_API_KEY")
        self.model = model
        self.client = self._build_client()

    def _build_client(self):
        try:
            anthropic_module = importlib.import_module("anthropic")
        except ImportError as exc:
            raise RuntimeError("Anthropic SDK is not installed. Install the 'anthropic' package to use this provider.") from exc
        return anthropic_module.Anthropic(api_key=self.API_KEY)

    def _translate_history(self, history):
        messages = []
        for item in history:
            if isinstance(item, tuple) and len(item) == 2:
                role, content = item
            elif isinstance(item, dict):
                role = item.get("role")
                content = item.get("content") or item.get("parts", [{}])[0].get("text", "")
            else:
                continue
            if role == "model":
                role = "assistant"
            if role not in {"user", "assistant"}:
                continue
            if not content:
                continue
            messages.append({"role": role, "content": content})
        return messages

    def _translate_tools(self, tools):
        translated = []
        for tool in tools:
            translated.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                }
            )
        return translated

    def _maybe_append_native_web_search(self, translated_tools, enable_web_search):
        if not enable_web_search:
            return translated_tools
        return translated_tools + [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]

    def _extract_tool_calls(self, response):
        tool_calls = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "tool_use":
                tool_calls.append(
                    {
                        "id": getattr(block, "id", None),
                        "name": getattr(block, "name", "unknown_tool"),
                        "input": getattr(block, "input", {}) or {},
                    }
                )
        return tool_calls

    def _extract_text(self, response):
        text_parts = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "")
                if text:
                    text_parts.append(text)
        return "".join(text_parts)

    def _assistant_message_from_response(self, response):
        return {"role": "assistant", "content": getattr(response, "content", []) or []}

    def _run_tool_handler(self, tool_handler, tool_name, tool_args):
        if tool_handler is None:
            raise ValueError(f"Anthropic worker received tool call '{tool_name}' without a tool handler.")
        return tool_handler(tool_name, tool_args)

    def _tool_result_message(self, tool_calls, tool_handler):
        content = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "unknown_tool")
            tool_args = tool_call.get("input", {}) or {}
            tool_result = self._run_tool_handler(tool_handler, tool_name, tool_args)
            if not isinstance(tool_result, str):
                tool_result = json.dumps(tool_result)
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.get("id"),
                    "content": tool_result,
                }
            )
        return {"role": "user", "content": content}

    def _build_request_kwargs(self, system_prompt, translated_tools, messages, temperature, max_output_tokens):
        request_kwargs = {
            "model": self.model,
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_output_tokens or 4096,
        }
        if translated_tools:
            request_kwargs["tools"] = translated_tools
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        return request_kwargs

    def _emit_web_search_event_if_used(self, response, event_listener, round_number):
        if event_listener is None:
            return

        usage = getattr(response, "usage", None)
        server_tool_use = getattr(usage, "server_tool_use", None)
        request_count = getattr(server_tool_use, "web_search_requests", 0) if server_tool_use is not None else 0
        if request_count:
            event_listener(
                "web_search_used",
                {
                    "provider": "anthropic",
                    "count": request_count,
                    "round": round_number,
                },
            )

    def _request_until_not_paused(
        self,
        system_prompt,
        translated_tools,
        messages,
        temperature,
        max_output_tokens,
        event_listener,
        round_number,
    ):
        response = self.client.messages.create(
            **self._build_request_kwargs(
                system_prompt,
                translated_tools,
                messages,
                temperature,
                max_output_tokens,
            )
        )
        self._emit_web_search_event_if_used(response, event_listener, round_number)

        while getattr(response, "stop_reason", None) == "pause_turn":
            messages = messages + [self._assistant_message_from_response(response)]
            response = self.client.messages.create(
                **self._build_request_kwargs(
                    system_prompt,
                    translated_tools,
                    messages,
                    temperature,
                    max_output_tokens,
                )
            )
            self._emit_web_search_event_if_used(response, event_listener, round_number)

        return response, messages

    def _stream_response_until_not_paused(
        self,
        system_prompt,
        translated_tools,
        messages,
        temperature,
        max_output_tokens,
        event_listener,
        round_number,
    ):
        while True:
            request_kwargs = self._build_request_kwargs(
                system_prompt, translated_tools, messages, temperature, max_output_tokens,
            )
            with self.client.messages.stream(**request_kwargs) as stream:
                for event in stream:
                    if (
                        getattr(event, "type", None) == "content_block_delta"
                        and hasattr(event.delta, "text")
                        and event_listener is not None
                    ):
                        event_listener("text_delta", {
                            "provider": "anthropic",
                            "round": round_number,
                            "delta": event.delta.text,
                        })
                response = stream.get_final_message()
            self._emit_web_search_event_if_used(response, event_listener, round_number)
            if getattr(response, "stop_reason", None) != "pause_turn":
                return response, messages
            messages = messages + [self._assistant_message_from_response(response)]

    def generate_text(
        self,
        system_prompt,
        user_message,
        history=None,
        tools=None,
        tool_handler=None,
        temperature=None,
        max_output_tokens=None,
        max_tool_rounds=8,
        enable_web_search=False,
        event_listener=None,
        cancel_check=None,
        cache_config=None,
    ):
        # Anthropic prompt caching is intentionally not wired yet.
        # It requires explicit cache_control block placement rather than
        # reusing the OpenAI-style request-level cache hints.
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        messages = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})

        response, messages = self._request_until_not_paused(
            system_prompt,
            translated_tools,
            messages,
            temperature,
            max_output_tokens,
            event_listener,
            0,
        )
        invoke_cancel_check(cancel_check, "after_model_call")

        tool_calls = self._extract_tool_calls(response)
        tool_rounds = 0
        model_response = self._extract_text(response)

        while tool_calls:
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError("Anthropic worker exceeded tool call limit.")
            if event_listener is not None:
                event_listener(
                    "tool_calls_received",
                    {
                        "round": tool_rounds,
                        "count": len(tool_calls),
                        "names": [tool_call.get("name", "unknown_tool") for tool_call in tool_calls],
                    },
                )

            invoke_cancel_check(cancel_check, "before_tool_call")
            messages = messages + [
                self._assistant_message_from_response(response),
                self._tool_result_message(tool_calls, tool_handler),
            ]
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            invoke_cancel_check(cancel_check, "before_model_call")
            response, messages = self._request_until_not_paused(
                system_prompt,
                translated_tools,
                messages,
                temperature,
                max_output_tokens,
                event_listener,
                tool_rounds,
            )
            invoke_cancel_check(cancel_check, "after_model_call")
            model_response = self._extract_text(response) or model_response
            tool_calls = self._extract_tool_calls(response)

        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": tool_rounds})
        return model_response

    def generate_text_stream(
        self,
        system_prompt,
        user_message,
        history=None,
        tools=None,
        tool_handler=None,
        temperature=None,
        max_output_tokens=None,
        max_tool_rounds=8,
        enable_web_search=False,
        event_listener=None,
        cancel_check=None,
        cache_config=None,
    ):
        # cache_config accepted but not yet wired for Anthropic.
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        messages = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})

        response, messages = self._stream_response_until_not_paused(
            system_prompt,
            translated_tools,
            messages,
            temperature,
            max_output_tokens,
            event_listener,
            0,
        )
        invoke_cancel_check(cancel_check, "after_model_call")

        tool_calls = self._extract_tool_calls(response)
        tool_rounds = 0
        model_response = self._extract_text(response)

        while tool_calls:
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError("Anthropic worker exceeded tool call limit.")
            if event_listener is not None:
                event_listener(
                    "tool_calls_received",
                    {
                        "round": tool_rounds,
                        "count": len(tool_calls),
                        "names": [tool_call.get("name", "unknown_tool") for tool_call in tool_calls],
                    },
                )

            invoke_cancel_check(cancel_check, "before_tool_call")
            messages = messages + [
                self._assistant_message_from_response(response),
                self._tool_result_message(tool_calls, tool_handler),
            ]
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            invoke_cancel_check(cancel_check, "before_model_call")
            response, messages = self._stream_response_until_not_paused(
                system_prompt,
                translated_tools,
                messages,
                temperature,
                max_output_tokens,
                event_listener,
                tool_rounds,
            )
            invoke_cancel_check(cancel_check, "after_model_call")
            model_response = self._extract_text(response) or model_response
            tool_calls = self._extract_tool_calls(response)

        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": tool_rounds})
        return model_response


AnthropicWorker = AnthropicCaller
