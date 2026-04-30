import importlib
import json
import os

from orchestration.run_control import invoke_cancel_check
from providers.structured_output import (
    is_valid_json_text,
    require_output_schema,
    warning_payload,
)
from providers.step_protocol import ProviderStepResult, ProviderToolCall
from utils.env import load_project_dotenv


class GoogleCaller:
    def __init__(self, model):
        load_project_dotenv()
        self.model = model
        self.client = self._build_client()

    def _build_client(self):
        try:
            genai_module = importlib.import_module("google.genai")
        except ImportError as exc:
            raise RuntimeError(
                "Google Gen AI SDK is not installed. Install the 'google-genai' package to use this provider."
            ) from exc

        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            return genai_module.Client(api_key=api_key)
        return genai_module.Client()

    def _get_value(self, obj, *names, default=None):
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj.get(name)
            if hasattr(obj, name):
                return getattr(obj, name)
        return default

    def _translate_history(self, history):
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

            if role == "assistant":
                role = "model"
            if role not in {"user", "model"}:
                continue
            text = str(content or "")
            if not text:
                continue
            translated.append({"role": role, "parts": [{"text": text}]})
        return translated

    def _translate_tools(self, tools):
        if not tools:
            return []
        return [
            {
                "function_declarations": [
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters_json_schema": tool["parameters"],
                    }
                    for tool in tools
                ]
            }
        ]

    def _build_config(
        self,
        system_prompt,
        tools,
        temperature,
        max_output_tokens,
        structured_output=False,
        output_schema=None,
    ):
        config = {}
        if system_prompt:
            config["system_instruction"] = system_prompt
        translated_tools = self._translate_tools(tools or [])
        if translated_tools:
            config["tools"] = translated_tools
            config["automatic_function_calling"] = {"disable": True}
        if temperature is not None:
            config["temperature"] = temperature
        if max_output_tokens is not None:
            config["max_output_tokens"] = max_output_tokens
        if structured_output:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = output_schema
        return config

    def _extract_text(self, response):
        text = self._get_value(response, "text")
        if isinstance(text, str) and text:
            return text

        text_parts = []
        for part in self._extract_parts_from_response(response):
            part_text = self._get_value(part, "text")
            if isinstance(part_text, str) and part_text:
                text_parts.append(part_text)
        return "".join(text_parts)

    def _extract_parts_from_response(self, response):
        candidate = None
        candidates = self._get_value(response, "candidates", default=[]) or []
        if candidates:
            candidate = candidates[0]
        content = (
            self._get_value(candidate, "content") if candidate is not None else None
        )
        return list(self._get_value(content, "parts", default=[]) or [])

    def _extract_tool_calls(self, response):
        function_calls = self._get_value(response, "function_calls", default=None)
        if function_calls:
            return list(function_calls)

        tool_calls = []
        for part in self._extract_parts_from_response(response):
            function_call = self._get_value(part, "function_call", "functionCall")
            if function_call is not None:
                tool_calls.append(function_call)
        return tool_calls

    def _normalize_tool_call(self, tool_call):
        arguments = self._get_value(tool_call, "args", "arguments", default={}) or {}
        return ProviderToolCall(
            name=self._get_value(tool_call, "name", default="unknown_tool"),
            arguments=self._parse_tool_arguments(arguments),
            call_id=self._get_value(tool_call, "id", "call_id"),
        )

    def _response_to_model_content(self, response):
        parts = []
        for part in self._extract_parts_from_response(response):
            text = self._get_value(part, "text")
            if isinstance(text, str) and text:
                parts.append({"text": text})
                continue

            function_call = self._get_value(part, "function_call", "functionCall")
            if function_call is not None:
                parts.append(
                    {
                        "function_call": {
                            "name": self._get_value(
                                function_call, "name", default="unknown_tool"
                            ),
                            "args": self._parse_tool_arguments(
                                self._get_value(
                                    function_call, "args", "arguments", default={}
                                )
                                or {}
                            ),
                            "id": self._get_value(function_call, "id", "call_id"),
                        }
                    }
                )
        if not parts:
            return None
        return {"role": "model", "parts": parts}

    def _step_result_from_response(self, response, contents):
        session_contents = list(contents or [])
        model_content = self._response_to_model_content(response)
        if model_content is not None:
            session_contents.append(model_content)
        return ProviderStepResult(
            output_text=self._extract_text(response),
            tool_calls=[
                self._normalize_tool_call(tool_call)
                for tool_call in self._extract_tool_calls(response)
            ],
            session_state={"contents": session_contents},
        )

    def _parse_tool_arguments(self, arguments):
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {"raw_arguments": arguments}
        return {}

    def _run_tool_handler(self, tool_handler, tool_name, tool_args):
        if tool_handler is None:
            raise ValueError(
                f"Google worker received tool call '{tool_name}' without a tool handler."
            )
        return tool_handler(tool_name, tool_args)

    def _tool_result_contents_from_calls(self, tool_calls, tool_handler):
        contents = []
        for tool_call in tool_calls:
            tool_name = self._get_value(tool_call, "name", default="unknown_tool")
            tool_args = self._parse_tool_arguments(
                self._get_value(tool_call, "args", "arguments", default={}) or {}
            )
            tool_result = self._run_tool_handler(tool_handler, tool_name, tool_args)
            if not isinstance(tool_result, dict):
                tool_result = {"output": tool_result}
            contents.append(
                {
                    "role": "tool",
                    "parts": [
                        {
                            "function_response": {
                                "name": tool_name,
                                "response": tool_result,
                            }
                        }
                    ],
                }
            )
        return contents

    def _tool_result_contents_from_results(self, tool_results):
        contents = []
        for tool_result in tool_results or []:
            output = tool_result.get("output", "")
            if not isinstance(output, dict):
                output = {"output": output}
            contents.append(
                {
                    "role": "tool",
                    "parts": [
                        {
                            "function_response": {
                                "name": tool_result.get("name", "unknown_tool"),
                                "response": output,
                            }
                        }
                    ],
                }
            )
        return contents

    def _emit_structured_output_warning(
        self, event_listener, output_schema, reason, used_prompt_fallback
    ):
        if event_listener is None:
            return
        event_listener(
            "structured_output_warning",
            warning_payload(
                provider="google",
                model=self.model,
                output_schema=output_schema,
                reason=reason,
                native_method="generate_content.config.response_schema",
                used_prompt_fallback=used_prompt_fallback,
            ),
        )

    def _emit_web_search_unsupported(self, event_listener, round_number):
        if event_listener is None:
            return
        event_listener(
            "web_search_unsupported", {"provider": "google", "round": round_number}
        )

    def _request_content(
        self,
        *,
        contents,
        config,
        event_listener,
        round_number,
        structured_output=False,
        output_schema=None,
    ):
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            if not structured_output:
                raise
            self._emit_structured_output_warning(
                event_listener, output_schema, str(exc), used_prompt_fallback=True
            )
            fallback_config = dict(config)
            fallback_config.pop("response_mime_type", None)
            fallback_config.pop("response_schema", None)
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=fallback_config,
            )

        if structured_output:
            response_text = self._extract_text(response)
            if response_text and not is_valid_json_text(response_text):
                self._emit_structured_output_warning(
                    event_listener,
                    output_schema,
                    "native structured output returned invalid JSON",
                    used_prompt_fallback=False,
                )
        return response

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
        del cache_config
        contents = self._translate_history(history or []) + [
            {"role": "user", "parts": [{"text": user_message}]}
        ]
        config = self._build_config(
            system_prompt, tools or [], temperature, max_output_tokens
        )

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        if enable_web_search:
            self._emit_web_search_unsupported(event_listener, round_number)
        response = self._request_content(
            contents=contents,
            config=config,
            event_listener=event_listener,
            round_number=round_number,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        return self._step_result_from_response(response, contents)

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
        del cache_config
        session_state = session_state or {}
        contents = list(session_state.get("contents") or [])
        contents.extend(self._tool_result_contents_from_results(tool_results))
        config = self._build_config(
            system_prompt, tools or [], temperature, max_output_tokens
        )

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        if enable_web_search:
            self._emit_web_search_unsupported(event_listener, round_number)
        response = self._request_content(
            contents=contents,
            config=config,
            event_listener=event_listener,
            round_number=round_number,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        return self._step_result_from_response(response, contents)

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
        del cache_config
        output_schema = require_output_schema(structured_output, output_schema)
        contents = self._translate_history(history or []) + [
            {"role": "user", "parts": [{"text": user_message}]}
        ]
        config = self._build_config(
            system_prompt,
            tools or [],
            temperature,
            max_output_tokens,
            structured_output=structured_output,
            output_schema=output_schema,
        )

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})
        if enable_web_search:
            self._emit_web_search_unsupported(event_listener, 0)
        response = self._request_content(
            contents=contents,
            config=config,
            event_listener=event_listener,
            round_number=0,
            structured_output=structured_output,
            output_schema=output_schema,
        )
        invoke_cancel_check(cancel_check, "after_model_call")

        model_response = self._extract_text(response)
        tool_calls = self._extract_tool_calls(response)
        tool_rounds = 0

        while tool_calls:
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError("Google worker exceeded tool call limit.")
            if event_listener is not None:
                event_listener(
                    "tool_calls_received",
                    {
                        "round": tool_rounds,
                        "count": len(tool_calls),
                        "names": [
                            self._get_value(tool_call, "name", default="unknown_tool")
                            for tool_call in tool_calls
                        ],
                    },
                )

            invoke_cancel_check(cancel_check, "before_tool_call")
            model_content = self._response_to_model_content(response)
            if model_content is not None:
                contents.append(model_content)
            contents.extend(
                self._tool_result_contents_from_calls(tool_calls, tool_handler)
            )
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            invoke_cancel_check(cancel_check, "before_model_call")
            if event_listener is not None:
                event_listener("request_submitted", {"round": tool_rounds})
            if enable_web_search:
                self._emit_web_search_unsupported(event_listener, tool_rounds)
            response = self._request_content(
                contents=contents,
                config=config,
                event_listener=event_listener,
                round_number=tool_rounds,
                structured_output=structured_output,
                output_schema=output_schema,
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
        structured_output=False,
        output_schema=None,
    ):
        if structured_output or tools:
            return self.generate_text(
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
                tools=tools,
                tool_handler=tool_handler,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                max_tool_rounds=max_tool_rounds,
                enable_web_search=enable_web_search,
                event_listener=event_listener,
                cancel_check=cancel_check,
                cache_config=cache_config,
                structured_output=structured_output,
                output_schema=output_schema,
            )

        del cache_config
        contents = self._translate_history(history or []) + [
            {"role": "user", "parts": [{"text": user_message}]}
        ]
        config = self._build_config(system_prompt, [], temperature, max_output_tokens)

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})
        if enable_web_search:
            self._emit_web_search_unsupported(event_listener, 0)

        chunks = self.client.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        )
        collected = []
        for chunk in chunks:
            delta = self._extract_text(chunk)
            if not delta:
                continue
            collected.append(delta)
            if event_listener is not None:
                event_listener(
                    "text_delta",
                    {
                        "provider": "google",
                        "round": 0,
                        "delta": delta,
                    },
                )

        invoke_cancel_check(cancel_check, "after_model_call")
        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": 0})
        return "".join(collected)


GoogleWorker = GoogleCaller
