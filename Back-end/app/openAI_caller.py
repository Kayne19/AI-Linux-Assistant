import json

from dotenv import load_dotenv
from openai import OpenAI


class OpenAICaller:
    def __init__(self, model="gpt-4.1-mini", reasoning_effort=None):
        load_dotenv()
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

    def _translate_tools(self, tools):
        translated = []
        for tool in tools:
            translated.append(
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                    "strict": tool.get("strict", True),
                }
            )
        return translated

    def _extract_tool_calls(self, response):
        tool_calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                tool_calls.append(item)
        return tool_calls

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

    def _build_tool_outputs(self, tool_calls, tool_handler):
        outputs = []
        for tool_call in tool_calls:
            tool_name = getattr(tool_call, "name", "unknown_tool")
            tool_args = self._parse_tool_arguments(getattr(tool_call, "arguments", {}))
            tool_result = self._run_tool_handler(tool_handler, tool_name, tool_args)
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

    def _build_request_kwargs(self, system_prompt, translated_tools, temperature, max_output_tokens):
        request_kwargs = {
            "model": self.model,
            "instructions": system_prompt,
        }
        if self.reasoning_effort:
            request_kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if translated_tools:
            request_kwargs["tools"] = translated_tools
            request_kwargs["parallel_tool_calls"] = True
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if max_output_tokens is not None:
            request_kwargs["max_output_tokens"] = max_output_tokens
        return request_kwargs

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
        event_listener=None,
    ):
        translated_tools = self._translate_tools(tools or [])
        request_kwargs = self._build_request_kwargs(
            system_prompt,
            translated_tools,
            temperature,
            max_output_tokens,
        )
        request_kwargs["input"] = self._translate_history(history or []) + [{"role": "user", "content": user_message}]

        if event_listener is not None:
            event_listener("request_submitted", {"round": 0})
        response = self.client.responses.create(**request_kwargs)
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

            followup_kwargs = self._build_request_kwargs(
                system_prompt,
                translated_tools,
                temperature,
                max_output_tokens,
            )
            followup_kwargs["previous_response_id"] = response.id
            followup_kwargs["input"] = self._build_tool_outputs(tool_calls, tool_handler)
            if event_listener is not None:
                event_listener("tool_results_submitted", {"round": tool_rounds})

            response = self.client.responses.create(**followup_kwargs)
            tool_calls = self._extract_tool_calls(response)

        if event_listener is not None:
            event_listener("response_completed", {"tool_rounds": tool_rounds})
        return response.output_text or ""


OpenAIWorker = OpenAICaller
openAICaller = OpenAICaller
