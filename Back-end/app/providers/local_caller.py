import json

from orchestration.run_control import invoke_cancel_check
from providers.structured_output import require_output_schema, warning_payload
from providers.step_protocol import ProviderStepResult, ProviderToolCall

try:
    import ollama
except (
    ImportError
):  # pragma: no cover - optional dependency in some test/runtime environments
    ollama = None

from utils.debug_utils import debug_print


class LocalCaller:
    # "qwen2.5:7b"
    # "llama3.1:8b"
    # "mannix/llama3.1-8b-abliterated"
    # "mistral-nemo"
    def __init__(self, model="qwen2.5:7b"):
        self.model = model

    def _emit_structured_output_warning(self, event_listener, output_schema):
        if event_listener is None:
            return
        event_listener(
            "structured_output_warning",
            warning_payload(
                provider="local",
                model=self.model,
                output_schema=output_schema,
                reason="native structured output unsupported",
                native_method="none",
                used_prompt_fallback=True,
            ),
        )

    # Deprecated: prefer start_text_step / continue_text_step for single-step transport.
    # This convenience wrapper keeps its own tool-call loop for backwards compatibility.
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
        structured_output=False,
        output_schema=None,
    ):
        output_schema = require_output_schema(structured_output, output_schema)
        system_message = {
            "role": "system",
            "content": system_prompt,
        }
        history_messages = self.translate_history(history or [])
        messages = (
            [system_message]
            + history_messages
            + [{"role": "user", "content": user_message}]
        )
        translated_tools = self._translate_tools(tools or [])
        self._tool_handler = tool_handler

        debug_print(
            "\n" + ">" * 20 + f" [DEBUG] SENDING TO LOCAL {self.model} " + ">" * 20
        )
        debug_print(f"Sending {len(messages)} messages...")
        debug_print(f"[AI Debug Print] Prompt chars: {len(user_message)}")
        debug_print("<" * 20 + " END PAYLOAD " + "<" * 20 + "\n")
        if structured_output:
            self._emit_structured_output_warning(event_listener, output_schema)

        try:
            if ollama is None:
                raise RuntimeError(
                    "Ollama SDK is not installed. Install the 'ollama' package to use this provider."
                )
            invoke_cancel_check(cancel_check, "before_model_call")
            if event_listener is not None:
                event_listener("request_submitted", {"round": 0})
            request_kwargs = {
                "model": self.model,
                "messages": messages,
            }
            if translated_tools:
                request_kwargs["tools"] = translated_tools
            if temperature is not None:
                request_kwargs["options"] = {"temperature": temperature}

            response = ollama.chat(**request_kwargs)
            invoke_cancel_check(cancel_check, "after_model_call")
            message = response.get("message", {})
            model_response = message.get("content", "")
            tool_calls = self._normalize_tool_calls(message.get("tool_calls") or [])
            tool_rounds = 0

            while tool_calls:
                tool_rounds += 1
                if tool_rounds > max_tool_rounds:
                    raise RuntimeError("Local worker exceeded tool call limit.")
                debug_print(f"[TOOL DEBUG] Tool calls returned: {len(tool_calls)}")
                debug_print(
                    f"[TOOL DEBUG] Raw tool_calls: {json.dumps(tool_calls, indent=2, default=str)}"
                )
                if event_listener is not None:
                    event_listener(
                        "tool_calls_received",
                        {
                            "round": tool_rounds,
                            "count": len(tool_calls),
                            "names": [
                                tool_call.get("function", {}).get(
                                    "name", "unknown_tool"
                                )
                                for tool_call in tool_calls
                            ],
                        },
                    )
                if self._tool_handler is None:
                    raise ValueError(
                        "Local worker received tool calls without a tool handler."
                    )

                invoke_cancel_check(cancel_check, "before_tool_call")
                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content", ""),
                    "tool_calls": tool_calls,
                }
                tool_messages = []
                for tool_call in tool_calls:
                    debug_print(
                        f"[TOOL DEBUG] Handling tool_call: {json.dumps(tool_call, indent=2, default=str)}"
                    )
                    tool_messages.append(self._build_tool_message(tool_call))
                debug_print(
                    f"[TOOL DEBUG] Tool messages: {json.dumps(tool_messages, indent=2, default=str)}"
                )

                messages = messages + [assistant_message] + tool_messages
                debug_print("[TOOL DEBUG] Sending follow-up request with tool results.")
                if event_listener is not None:
                    event_listener("tool_results_submitted", {"round": tool_rounds})
                followup_kwargs = {
                    "model": self.model,
                    "messages": messages,
                }
                if translated_tools:
                    followup_kwargs["tools"] = translated_tools
                if temperature is not None:
                    followup_kwargs["options"] = {"temperature": temperature}
                invoke_cancel_check(cancel_check, "before_model_call")
                response = ollama.chat(**followup_kwargs)
                invoke_cancel_check(cancel_check, "after_model_call")
                message = response.get("message", {})
                model_response = message.get("content", model_response)
                tool_calls = self._normalize_tool_calls(message.get("tool_calls") or [])

            if event_listener is not None:
                event_listener("response_completed", {"tool_rounds": tool_rounds})
            return model_response

        except Exception as e:
            raise RuntimeError(f"Ollama Error: {str(e)}") from e

    def start_text_step(
        self,
        system_prompt,
        user_message,
        history=None,
        tools=None,
        temperature=None,
        max_output_tokens=None,
        enable_web_search=False,
        event_listener=None,
        cancel_check=None,
        cache_config=None,
        round_number=0,
    ):
        del max_output_tokens, enable_web_search, cache_config
        system_message = {
            "role": "system",
            "content": system_prompt,
        }
        history_messages = self.translate_history(history or [])
        messages = (
            [system_message]
            + history_messages
            + [{"role": "user", "content": user_message}]
        )
        translated_tools = self._translate_tools(tools or [])

        if ollama is None:
            raise RuntimeError(
                "Ollama SDK is not installed. Install the 'ollama' package to use this provider."
            )
        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        request_kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if translated_tools:
            request_kwargs["tools"] = translated_tools
        if temperature is not None:
            request_kwargs["options"] = {"temperature": temperature}
        response = ollama.chat(**request_kwargs)
        invoke_cancel_check(cancel_check, "after_model_call")
        return self._step_result_from_message(
            response.get("message", {}),
            messages,
        )

    def continue_text_step(
        self,
        system_prompt,
        session_state,
        tool_results,
        tools=None,
        temperature=None,
        max_output_tokens=None,
        enable_web_search=False,
        event_listener=None,
        cancel_check=None,
        cache_config=None,
        round_number=1,
    ):
        del system_prompt, max_output_tokens, enable_web_search, cache_config
        if ollama is None:
            raise RuntimeError(
                "Ollama SDK is not installed. Install the 'ollama' package to use this provider."
            )
        session_state = session_state or {}
        messages = list(session_state.get("messages") or [])
        assistant_message = dict(session_state.get("assistant_message") or {})
        if assistant_message:
            messages.append(assistant_message)
        messages.extend(self._tool_messages_from_results(tool_results))

        translated_tools = self._translate_tools(tools or [])
        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        request_kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if translated_tools:
            request_kwargs["tools"] = translated_tools
        if temperature is not None:
            request_kwargs["options"] = {"temperature": temperature}
        response = ollama.chat(**request_kwargs)
        invoke_cancel_check(cancel_check, "after_model_call")
        return self._step_result_from_message(
            response.get("message", {}),
            messages,
        )

    def translate_history(self, history):
        translated = []
        for item in history:
            if isinstance(item, tuple) and len(item) == 2:
                role, content = item
            elif isinstance(item, dict):
                role = item.get("role")
                content = item.get("content") or item.get("parts", [{}])[0].get(
                    "text", ""
                )
            else:
                continue
            if role == "model":
                role = "assistant"
            if not role:
                continue
            translated.append({"role": role, "content": content})
        return translated

    def _translate_tools(self, tools):
        translated = []
        for tool in tools:
            translated.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
            )
        return translated

    def _normalize_tool_calls(self, tool_calls):
        if isinstance(tool_calls, dict):
            tool_calls = [tool_calls]
        if not isinstance(tool_calls, list):
            tool_calls = []
        normalized_tool_calls = []
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                normalized_tool_calls.append(tool_call)
            elif hasattr(tool_call, "model_dump"):
                normalized_tool_calls.append(tool_call.model_dump())
            elif hasattr(tool_call, "dict"):
                normalized_tool_calls.append(tool_call.dict())
            elif hasattr(tool_call, "__dict__"):
                normalized_tool_calls.append(tool_call.__dict__)
            else:
                normalized_tool_calls.append({"raw_tool_call": str(tool_call)})
        return normalized_tool_calls

    def _normalize_provider_tool_call(self, tool_call):
        function = tool_call.get("function", {})
        return ProviderToolCall(
            name=function.get("name", "unknown_tool"),
            arguments=self._parse_tool_arguments(function.get("arguments", {})),
            call_id=tool_call.get("id"),
        )

    def _step_result_from_message(self, message, messages):
        normalized_tool_calls = self._normalize_tool_calls(
            message.get("tool_calls") or []
        )
        assistant_message = {
            "role": "assistant",
            "content": message.get("content", ""),
        }
        if normalized_tool_calls:
            assistant_message["tool_calls"] = normalized_tool_calls
        return ProviderStepResult(
            output_text=message.get("content", ""),
            tool_calls=[
                self._normalize_provider_tool_call(tool_call)
                for tool_call in normalized_tool_calls
            ],
            session_state={
                "messages": list(messages or []),
                "assistant_message": assistant_message,
            },
        )

    def _tool_messages_from_results(self, tool_results):
        messages = []
        for tool_result in tool_results or []:
            output = tool_result.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output)
            tool_message = {
                "role": "tool",
                "name": tool_result.get("name", "unknown_tool"),
                "content": output,
            }
            tool_call_id = tool_result.get("call_id")
            if tool_call_id:
                tool_message["tool_call_id"] = tool_call_id
            messages.append(tool_message)
        return messages

    def _build_tool_message(self, tool_call):
        function = tool_call.get("function", {})
        tool_name = function.get("name", "unknown_tool")
        tool_args = self._parse_tool_arguments(function.get("arguments", {}))

        debug_print(f"[TOOL DEBUG] Parsed tool args for {tool_name}: {tool_args}")
        tool_result = self._run_tool(tool_name, tool_args)
        if not isinstance(tool_result, str):
            tool_result = json.dumps(tool_result)
        debug_print(f"[TOOL DEBUG] Tool result for {tool_name}: {tool_result}")

        tool_message = {
            "role": "tool",
            "name": tool_name,
            "content": tool_result,
        }
        tool_call_id = tool_call.get("id")
        if tool_call_id:
            tool_message["tool_call_id"] = tool_call_id

        return tool_message

    def _parse_tool_arguments(self, arguments):
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {"raw_arguments": arguments}
        return {}

    def _run_tool(self, tool_name, tool_args):
        if self._tool_handler is None:
            return f"Tool error: missing handler for '{tool_name}'"
        try:
            return self._tool_handler(tool_name, tool_args)
        except Exception as exc:
            return f"Tool error: {exc}"


LocalWorker = LocalCaller
