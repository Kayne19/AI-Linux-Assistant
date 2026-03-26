import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from debug_utils import debug_print


# --- SETUP ---
class GeminiCaller:
    # gemini-3-flash-preview
    # gemma-3-27b
    def __init__(self, model="gemma-3-27b"):
        load_dotenv()
        self.API_KEY = os.getenv("GOOGLE_API_KEY")
        self.model = model
        self.client = genai.Client(api_key=self.API_KEY)

    def translate_history(self, history):
        translated = []
        for item in history:
            if isinstance(item, tuple) and len(item) == 2:
                role, content = item
            elif isinstance(item, dict):
                role = item.get("role")
                content = item.get("content") or item.get("parts", [{}])[0].get("text", "")
            else:
                continue
            if role == "assistant":
                role = "model"
            if not role:
                continue
            translated.append(types.Content(role=role, parts=[types.Part(text=content)]))
        return translated

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
        current_turn_content = f"""
        SYSTEM INSTRUCTION:
        {system_prompt}

        {user_message}
        """

        current_message = types.Content(
            role="user",
            parts=[types.Part(text=current_turn_content)],
        )

        payload_messages = self.translate_history(history or []) + [current_message]
        config_kwargs = {
            "max_output_tokens": max_output_tokens or 2048 * 2,
        }
        translated_tools = self._translate_tools(tools or [])
        if translated_tools:
            config_kwargs["tools"] = translated_tools
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        self.tool_handler = tool_handler

        try:
            if event_listener is not None:
                event_listener("request_submitted", {"round": 0})
            response = self.client.models.generate_content(
                model=self.model,
                contents=payload_messages,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            tool_rounds = 0
            tool_calls, tool_call_content = self.extract_tool_calls(response)
            model_response = response.text or ""

            while tool_calls:
                tool_rounds += 1
                if tool_rounds > max_tool_rounds:
                    raise RuntimeError("Gemini worker exceeded tool call limit.")
                if event_listener is not None:
                    event_listener(
                        "tool_calls_received",
                        {
                            "round": tool_rounds,
                            "count": len(tool_calls),
                            "names": [tool_call.get("name", "unknown_tool") for tool_call in tool_calls],
                        },
                    )
                if tool_handler is None:
                    raise ValueError("Gemini worker received tool calls without a tool handler.")

                tool_response_content = self.build_tool_response_content(tool_calls)
                payload_messages = payload_messages + [tool_call_content, tool_response_content]
                if event_listener is not None:
                    event_listener("tool_results_submitted", {"round": tool_rounds})

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=payload_messages,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                model_response = response.text or model_response
                tool_calls, tool_call_content = self.extract_tool_calls(response)

            if event_listener is not None:
                event_listener("response_completed", {"tool_rounds": tool_rounds})
            return model_response
            
        except (KeyError, IndexError, AttributeError) as e:
            debug_print(f"Gemini Error: {e}")
            raise RuntimeError("Gemini worker could not parse response.") from e

    def _translate_tools(self, tools):
        if not tools:
            return []
        declarations = []
        for tool in tools:
            declarations.append(
                types.FunctionDeclaration(
                    name=tool["name"],
                    description=tool["description"],
                    parameters=tool["parameters"],
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def extract_tool_calls(self, response):
        tool_calls = []
        tool_call_content = None
        try:
            tool_call_content = response.candidates[0].content
            parts = tool_call_content.parts
            for part in parts:
                function_call = getattr(part, "function_call", None)
                if function_call:
                    tool_calls.append({
                        "name": function_call.name,
                        "arguments": function_call.args,
                    })
        except (AttributeError, IndexError, TypeError):
            return [], None
        return tool_calls, tool_call_content

    def build_tool_response_content(self, tool_calls):
        response_parts = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("name")
            tool_args = self.parse_tool_arguments(tool_call.get("arguments"))
            tool_result = self.run_tool(tool_name, tool_args)
            if not isinstance(tool_result, dict):
                tool_result = {"result": tool_result}
            response_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=tool_name,
                        response=tool_result,
                    )
                )
            )
        return types.Content(role="user", parts=response_parts)

    def parse_tool_arguments(self, arguments):
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except json.JSONDecodeError:
                return {"raw_arguments": arguments}
        return {}

    def run_tool(self, tool_name, tool_args):
        if self.tool_handler is None:
            return {"error": f"tool handler missing for '{tool_name}'"}
        return self.tool_handler(tool_name, tool_args)

    @property
    def tool_handler(self):
        return getattr(self, "_tool_handler", None)

    @tool_handler.setter
    def tool_handler(self, handler):
        self._tool_handler = handler


GeminiWorker = GeminiCaller
