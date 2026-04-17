import json
import hashlib
import re
import time

from orchestration.run_control import invoke_cancel_check
from providers.structured_output import (
    is_valid_json_text,
    require_output_schema,
    schema_name,
    warning_payload,
)
from providers.step_protocol import ProviderStepResult, ProviderToolCall

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    def load_dotenv():
        return False

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency in some test/runtime environments
    OpenAI = None


class OpenAICaller:
    def __init__(self, model="gpt-4.1-mini", reasoning_effort=None):
        load_dotenv()
        if OpenAI is None:
            raise RuntimeError("OpenAI SDK is not installed. Install the 'openai' package to use this provider.")
        self.client = OpenAI()
        self.model = model
        self.reasoning_effort = reasoning_effort or None

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
            if not role:
                continue
            messages.append({"role": role, "content": content})
        return messages

    def _schema_allows_null(self, schema):
        if not isinstance(schema, dict):
            return False
        schema_type = schema.get("type")
        if schema_type == "null":
            return True
        if isinstance(schema_type, list) and "null" in schema_type:
            return True
        if None in (schema.get("enum") or []):
            return True
        for variant_key in ("anyOf", "oneOf"):
            variants = schema.get(variant_key) or []
            if any(isinstance(variant, dict) and self._schema_allows_null(variant) for variant in variants):
                return True
        return False

    def _make_schema_nullable(self, schema):
        if self._schema_allows_null(schema):
            return schema
        return {"anyOf": [schema, {"type": "null"}]}

    def _normalize_strict_schema(self, schema):
        if isinstance(schema, list):
            return [self._normalize_strict_schema(item) for item in schema]
        if not isinstance(schema, dict):
            return schema

        normalized = {}
        for key, value in schema.items():
            if key in {"properties", "items", "anyOf", "oneOf", "allOf"}:
                continue
            normalized[key] = value

        properties = schema.get("properties")
        if isinstance(properties, dict):
            original_required = set(schema.get("required") or [])
            normalized_properties = {}
            for name, subschema in properties.items():
                normalized_subschema = self._normalize_strict_schema(subschema)
                if name not in original_required:
                    normalized_subschema = self._make_schema_nullable(normalized_subschema)
                normalized_properties[name] = normalized_subschema
            normalized["properties"] = normalized_properties
            normalized["required"] = list(properties.keys())

        if "items" in schema:
            normalized["items"] = self._normalize_strict_schema(schema["items"])
        for variant_key in ("anyOf", "oneOf", "allOf"):
            if variant_key in schema:
                normalized[variant_key] = self._normalize_strict_schema(schema[variant_key])
        return normalized

    def _translate_tools(self, tools):
        translated = []
        for tool in tools:
            strict = tool.get("strict", True)
            translated.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": self._normalize_strict_schema(tool["parameters"]) if strict else tool["parameters"],
                    "strict": strict,
                }
            )
        return translated

    def _maybe_append_native_web_search(self, translated_tools, enable_web_search):
        if not enable_web_search:
            return translated_tools
        return translated_tools + [{"type": "web_search"}]

    def _extract_tool_calls(self, response):
        tool_calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                tool_calls.append(item)
        return tool_calls

    def _normalize_tool_call(self, tool_call):
        return ProviderToolCall(
            name=getattr(tool_call, "name", "unknown_tool"),
            arguments=self._parse_tool_arguments(getattr(tool_call, "arguments", {})),
            call_id=getattr(tool_call, "call_id", None),
        )

    def _step_result_from_response(self, response):
        return ProviderStepResult(
            output_text=response.output_text or "",
            tool_calls=[self._normalize_tool_call(tool_call) for tool_call in self._extract_tool_calls(response)],
            session_state={"response_id": getattr(response, "id", None)},
        )

    def _extract_web_search_calls(self, response):
        web_search_calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "web_search_call":
                web_search_calls.append(item)
        return web_search_calls

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
            raise ValueError(f"OpenAI worker received tool call '{tool_name}' without a tool handler.")
        return tool_handler(tool_name, tool_args)

    def _build_tool_outputs(self, tool_calls, tool_handler, cancel_check=None):
        outputs = []
        for tool_call in tool_calls:
            tool_name = getattr(tool_call, "name", "unknown_tool")
            tool_args = self._parse_tool_arguments(getattr(tool_call, "arguments", {}))
            tool_result = self._run_tool_handler(tool_handler, tool_name, tool_args)
            invoke_cancel_check(cancel_check, f"after_tool:{tool_name}")
            if not isinstance(tool_result, str):
                tool_result = json.dumps(tool_result)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call.call_id,
                    "output": tool_result,
                }
            )
        return outputs

    def _translate_tool_results(self, tool_results):
        outputs = []
        for tool_result in tool_results or []:
            output = tool_result.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_result.get("call_id"),
                    "output": output,
                }
            )
        return outputs

    def _build_structured_output_kwargs(self, output_schema):
        normalized_schema = self._normalize_strict_schema(output_schema)
        return {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name(output_schema),
                    "schema": normalized_schema,
                    "strict": True,
                }
            }
        }

    def _build_request_kwargs(
        self,
        system_prompt,
        translated_tools,
        temperature,
        max_output_tokens,
        structured_output=False,
        output_schema=None,
    ):
        request_kwargs = {
            "model": self.model,
            "instructions": system_prompt,
        }
        if self.reasoning_effort:
            request_kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if translated_tools:
            request_kwargs["tools"] = translated_tools
            request_kwargs["parallel_tool_calls"] = True
        if temperature is not None and not self.reasoning_effort:
            request_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            request_kwargs["max_output_tokens"] = max_output_tokens
        if structured_output:
            request_kwargs.update(self._build_structured_output_kwargs(output_schema))
        return request_kwargs

    def _build_prompt_cache_kwargs(self, system_prompt, translated_tools, cache_config):
        if not cache_config or not cache_config.get("enabled", True):
            return {}

        scope = cache_config.get("scope", "default")
        retention = cache_config.get("retention")
        key_suffix = cache_config.get("key_suffix", "")
        static_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "model": self.model,
                    "system_prompt": system_prompt,
                    "tools": translated_tools,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        prompt_cache_key = f"{scope}:{static_fingerprint}"
        if key_suffix:
            prompt_cache_key = f"{prompt_cache_key}:{key_suffix}"

        kwargs = {"prompt_cache_key": prompt_cache_key}
        if retention:
            kwargs["prompt_cache_retention"] = retention
        return kwargs

    def _extract_prompt_cache_metrics(self, response):
        usage = getattr(response, "usage", None)
        if usage is None:
            return None

        input_details = getattr(usage, "input_tokens_details", None)
        cached_tokens = getattr(input_details, "cached_tokens", 0) if input_details is not None else 0
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        return {
            "cached_tokens": cached_tokens or 0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _emit_prompt_cache_metrics_if_present(self, response, event_listener, round_number):
        if event_listener is None:
            return

        metrics = self._extract_prompt_cache_metrics(response)
        if metrics is None:
            return
        event_listener(
            "prompt_cache_metrics",
            {
                "provider": "openai",
                "round": round_number,
                **metrics,
            },
        )

    def _emit_structured_output_warning(self, event_listener, output_schema, reason, used_prompt_fallback):
        if event_listener is None:
            return
        event_listener(
            "structured_output_warning",
            warning_payload(
                provider="openai",
                model=self.model,
                output_schema=output_schema,
                reason=reason,
                native_method="responses.text.format",
                used_prompt_fallback=used_prompt_fallback,
            ),
        )

    def _is_rate_limit_error(self, exc):
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True

        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 429:
            return True

        code = getattr(exc, "code", None)
        if code == "rate_limit_exceeded":
            return True

        message = str(exc).lower()
        return "rate limit" in message or "429" in message

    def _extract_retry_delay_seconds(self, exc, attempt_number):
        message = str(exc)
        match = re.search(r"try again in\s+(\d+(?:\.\d+)?)ms", message, re.IGNORECASE)
        if match:
            return max(float(match.group(1)) / 1000.0, 1.0)

        match = re.search(r"try again in\s+(\d+(?:\.\d+)?)s", message, re.IGNORECASE)
        if match:
            return max(float(match.group(1)), 1.0)

        return min(1.0 * (2 ** max(0, attempt_number - 1)), 80.0)

    def _create_response_with_retries(self, request_kwargs, event_listener=None, round_number=0, max_retries=12):
        attempt = 0
        while True:
            try:
                return self.client.responses.create(**request_kwargs)
            except Exception as exc:
                attempt += 1
                if not self._is_rate_limit_error(exc) or attempt > max_retries:
                    raise

                delay_seconds = self._extract_retry_delay_seconds(exc, attempt)
                if event_listener is not None:
                    event_listener(
                        "rate_limit_retry",
                        {
                            "provider": "openai",
                            "round": round_number,
                            "attempt": attempt,
                            "delay_seconds": delay_seconds,
                        },
                    )
                time.sleep(delay_seconds)

    def _stream_response_with_retries(self, request_kwargs, event_listener=None, round_number=0, max_retries=12):
        attempt = 0
        while True:
            emitted_text = False
            try:
                with self.client.responses.stream(**request_kwargs) as stream:
                    for event in stream:
                        if getattr(event, "type", None) == "response.output_text.delta":
                            emitted_text = True
                            if event_listener is not None:
                                event_listener(
                                    "text_delta",
                                    {
                                        "provider": "openai",
                                        "round": round_number,
                                        "delta": getattr(event, "delta", ""),
                                    },
                                )
                    return stream.get_final_response()
            except Exception as exc:
                attempt += 1
                if emitted_text or not self._is_rate_limit_error(exc) or attempt > max_retries:
                    raise

                delay_seconds = self._extract_retry_delay_seconds(exc, attempt)
                if event_listener is not None:
                    event_listener(
                        "rate_limit_retry",
                        {
                            "provider": "openai",
                            "round": round_number,
                            "attempt": attempt,
                            "delay_seconds": delay_seconds,
                        },
                )
                time.sleep(delay_seconds)

    def _create_text_response(
        self,
        request_kwargs,
        *,
        structured_output=False,
        output_schema=None,
        event_listener=None,
        round_number=0,
    ):
        try:
            response = self._create_response_with_retries(
                request_kwargs,
                event_listener=event_listener,
                round_number=round_number,
            )
        except Exception as exc:
            if not structured_output:
                raise
            self._emit_structured_output_warning(event_listener, output_schema, str(exc), used_prompt_fallback=True)
            fallback_kwargs = dict(request_kwargs)
            fallback_kwargs.pop("text", None)
            return self._create_response_with_retries(
                fallback_kwargs,
                event_listener=event_listener,
                round_number=round_number,
            )

        if structured_output and response.output_text and not is_valid_json_text(response.output_text):
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
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        request_kwargs = self._build_request_kwargs(
            system_prompt,
            translated_tools,
            temperature,
            max_output_tokens,
        )
        request_kwargs.update(
            self._build_prompt_cache_kwargs(
                system_prompt,
                translated_tools,
                cache_config,
            )
        )
        request_kwargs["input"] = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        response = self._create_response_with_retries(
            request_kwargs,
            event_listener=event_listener,
            round_number=round_number,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        self._emit_prompt_cache_metrics_if_present(response, event_listener, round_number)
        web_search_calls = self._extract_web_search_calls(response)
        if web_search_calls and event_listener is not None:
            event_listener(
                "web_search_used",
                {
                    "provider": "openai",
                    "count": len(web_search_calls),
                    "round": round_number,
                },
            )
        return self._step_result_from_response(response)

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
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        request_kwargs = self._build_request_kwargs(
            system_prompt,
            translated_tools,
            temperature,
            max_output_tokens,
        )
        request_kwargs.update(
            self._build_prompt_cache_kwargs(
                system_prompt,
                translated_tools,
                cache_config,
            )
        )
        request_kwargs["previous_response_id"] = (session_state or {}).get("response_id")
        request_kwargs["input"] = self._translate_tool_results(tool_results)

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": round_number})
        response = self._create_response_with_retries(
            request_kwargs,
            event_listener=event_listener,
            round_number=round_number,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        self._emit_prompt_cache_metrics_if_present(response, event_listener, round_number)
        web_search_calls = self._extract_web_search_calls(response)
        if web_search_calls and event_listener is not None:
            event_listener(
                "web_search_used",
                {
                    "provider": "openai",
                    "count": len(web_search_calls),
                    "round": round_number,
                },
            )
        return self._step_result_from_response(response)

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
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        request_kwargs = self._build_request_kwargs(
            system_prompt,
            translated_tools,
            temperature,
            max_output_tokens,
            structured_output=structured_output,
            output_schema=output_schema,
        )
        request_kwargs.update(
            self._build_prompt_cache_kwargs(
                system_prompt,
                translated_tools,
                cache_config,
            )
        )
        request_kwargs["input"] = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})
        response = self._create_text_response(
            request_kwargs,
            structured_output=structured_output,
            output_schema=output_schema,
            event_listener=event_listener,
            round_number=0,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        self._emit_prompt_cache_metrics_if_present(response, event_listener, 0)
        web_search_calls = self._extract_web_search_calls(response)
        if web_search_calls and event_listener is not None:
            event_listener(
                "web_search_used",
                {
                    "provider": "openai",
                    "count": len(web_search_calls),
                    "round": 0,
                },
            )
        tool_calls = self._extract_tool_calls(response)
        tool_rounds = 0

        while tool_calls:
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError("OpenAI worker exceeded tool call limit.")
            if event_listener is not None:
                event_listener(
                    "tool_calls_received",
                    {
                        "round": tool_rounds,
                        "count": len(tool_calls),
                        "names": [getattr(tool_call, "name", "unknown_tool") for tool_call in tool_calls],
                    },
                )

            invoke_cancel_check(cancel_check, "before_tool_call")
            followup_kwargs = self._build_request_kwargs(
                system_prompt,
                translated_tools,
                temperature,
                max_output_tokens,
                structured_output=structured_output,
                output_schema=output_schema,
            )
            followup_kwargs.update(
                self._build_prompt_cache_kwargs(
                    system_prompt,
                    translated_tools,
                    cache_config,
                )
            )
            followup_kwargs["previous_response_id"] = response.id
            followup_kwargs["input"] = self._build_tool_outputs(tool_calls, tool_handler, cancel_check=cancel_check)
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            invoke_cancel_check(cancel_check, "before_model_call")
            response = self._create_text_response(
                followup_kwargs,
                structured_output=structured_output,
                output_schema=output_schema,
                event_listener=event_listener,
                round_number=tool_rounds,
            )
            invoke_cancel_check(cancel_check, "after_model_call")
            self._emit_prompt_cache_metrics_if_present(response, event_listener, tool_rounds)
            web_search_calls = self._extract_web_search_calls(response)
            if web_search_calls and event_listener is not None:
                event_listener(
                    "web_search_used",
                    {
                        "provider": "openai",
                        "count": len(web_search_calls),
                        "round": tool_rounds,
                    },
                )
            tool_calls = self._extract_tool_calls(response)

        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": tool_rounds})
        return response.output_text or ""

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
        if structured_output:
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
        translated_tools = self._maybe_append_native_web_search(
            self._translate_tools(tools or []),
            enable_web_search,
        )
        request_kwargs = self._build_request_kwargs(
            system_prompt,
            translated_tools,
            temperature,
            max_output_tokens,
        )
        request_kwargs.update(
            self._build_prompt_cache_kwargs(
                system_prompt,
                translated_tools,
                cache_config,
            )
        )
        request_kwargs["input"] = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        invoke_cancel_check(cancel_check, "before_model_call")
        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})
        response = self._stream_response_with_retries(
            request_kwargs,
            event_listener=event_listener,
            round_number=0,
        )
        invoke_cancel_check(cancel_check, "after_model_call")
        self._emit_prompt_cache_metrics_if_present(response, event_listener, 0)
        web_search_calls = self._extract_web_search_calls(response)
        if web_search_calls and event_listener is not None:
            event_listener(
                "web_search_used",
                {
                    "provider": "openai",
                    "count": len(web_search_calls),
                    "round": 0,
                },
            )
        tool_calls = self._extract_tool_calls(response)
        tool_rounds = 0

        while tool_calls:
            tool_rounds += 1
            if tool_rounds > max_tool_rounds:
                raise RuntimeError("OpenAI worker exceeded tool call limit.")
            if event_listener is not None:
                event_listener(
                    "tool_calls_received",
                    {
                        "round": tool_rounds,
                        "count": len(tool_calls),
                        "names": [getattr(tool_call, "name", "unknown_tool") for tool_call in tool_calls],
                    },
                )

            invoke_cancel_check(cancel_check, "before_tool_call")
            followup_kwargs = self._build_request_kwargs(
                system_prompt,
                translated_tools,
                temperature,
                max_output_tokens,
            )
            followup_kwargs.update(
                self._build_prompt_cache_kwargs(
                    system_prompt,
                    translated_tools,
                    cache_config,
                )
            )
            followup_kwargs["previous_response_id"] = response.id
            followup_kwargs["input"] = self._build_tool_outputs(tool_calls, tool_handler, cancel_check=cancel_check)
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            invoke_cancel_check(cancel_check, "before_model_call")
            response = self._stream_response_with_retries(
                followup_kwargs,
                event_listener=event_listener,
                round_number=tool_rounds,
            )
            invoke_cancel_check(cancel_check, "after_model_call")
            self._emit_prompt_cache_metrics_if_present(response, event_listener, tool_rounds)
            web_search_calls = self._extract_web_search_calls(response)
            if web_search_calls and event_listener is not None:
                event_listener(
                    "web_search_used",
                    {
                        "provider": "openai",
                        "count": len(web_search_calls),
                        "round": tool_rounds,
                    },
                )
            tool_calls = self._extract_tool_calls(response)

        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": tool_rounds})
        return response.output_text or ""


OpenAIWorker = OpenAICaller
