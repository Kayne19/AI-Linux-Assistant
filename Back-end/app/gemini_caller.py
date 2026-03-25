import os
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
# --- SETUP ---
class GeminiCaller:
    # gemini-3-flash-preview
    # gemma-3-27b
    def __init__(self, router, system_prompt, model="gemma-3-27b"):
        load_dotenv()
        self.router = router
        self.system_prompt = system_prompt
        self.API_KEY = os.getenv("GOOGLE_API_KEY")
        self.model = model
        self.client = genai.Client(api_key=self.API_KEY)
        self.tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="search_RAG_database",
                        description="Search the RAG database for relevant context",
                        parameters={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Search query or question to look up",
                                },
                                "relevant_documents": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["debian", "proxmox", "docker", "general", "no_rag"],
                                    },
                                    "description": (
                                        "Document labels to search: debian, proxmox, "
                                        "docker, general, no_rag"
                                    ),
                                },
                            },
                            "required": ["query", "relevant_documents"],
                        },
                    )
                ]
            )
        ]

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

    def call_api(self, user_question, rag_context):
        
        current_turn_content = f"""
        SYSTEM INSTRUCTION:
        {self.system_prompt}

        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {rag_context}

        USER QUESTION:
        {user_question}
        """
        
        current_message = types.Content(
            role="user",
            parts=[types.Part(text=current_turn_content)],
        )

        payload_messages = self.translate_history(self.router.get_history()) + [current_message]
        
        '''
        # DEBUG: SEE EXACTLY WHAT YOU ARE SENDING
        print("\n" + ">"*20 + " [DEBUG] RAW OUTGOING PAYLOAD " + ">"*20)
        print(json.dumps(data, indent=2))
        print("<"*20 + " END PAYLOAD " + "<"*20 + "\n")
        # DEBUG: SEE EXACTLY WHAT YOU GOT BACK
        try:
            debug_response = response.json()
            print("\n" + ">"*20 + " [DEBUG] RAW INCOMING RESPONSE " + ">"*20)
            print(json.dumps(debug_response, indent=2))
            print("<"*20 + " END RESPONSE " + "<"*20 + "\n")
        except:
            print(f"\n❌ [DEBUG] RAW RESPONSE TEXT (Non-JSON): {response.text}")
        '''
        
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=payload_messages,
                config=types.GenerateContentConfig(
                    max_output_tokens=2048 * 2,
                    tools=self.tools,
                ),
            )

            tool_calls, tool_call_content = self.extract_tool_calls(response)
            if tool_calls:
                tool_response_content = self.build_tool_response_content(tool_calls)
                followup_messages = payload_messages + [tool_call_content, tool_response_content]
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=followup_messages,
                    config=types.GenerateContentConfig(
                        max_output_tokens=2048 * 2,
                        tools=self.tools,
                    ),
                )
                model_response = response.text or ""
                if not model_response:
                    model_response = f"Tool call requested: {json.dumps(tool_calls)}"
            else:
                model_response = response.text or ""
            
            # Update Permanent History (CLEANLY)
            # We only save the Question and the Answer. We discard the Context.
            self.router.update_history("user", user_question)
            self.router.update_history("model", model_response)
            
            
            return model_response
            
        except (KeyError, IndexError, AttributeError) as e:
            print(f"Gemini Error: {e}")
            return "Error: Could not parse response."

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
        if tool_name == "search_RAG_database":
            query = tool_args.get("query")
            relevant_documents = tool_args.get("relevant_documents")
            if query is None or relevant_documents is None:
                return {"error": "missing required arguments"}
            return self.search_rag_database(query, relevant_documents)
        return {"error": f"unknown tool '{tool_name}'"}

    def search_rag_database(self, retrieval_query, relevant_documents):
        return self.router.database.retrieve_context(retrieval_query, relevant_documents)
