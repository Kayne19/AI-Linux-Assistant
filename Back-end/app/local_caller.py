import json

import ollama

class LocalCaller:
    # "qwen2.5:7b"
    # "llama3.1:8b"
    # "mannix/llama3.1-8b-abliterated"
    # "mistral-nemo"
    def __init__(self, router, system_prompt, model="qwen2.5:7b"):
        self.model = model
        self.router = router
        self.system_prompt = system_prompt
        
        # We set the system prompt once. Ollama handles it as a message role.
        self.system_message = {
            "role": "system",
            "content": self.system_prompt
            }

    def call_api(self, user_question, rag_context):
        
        # 1. Construct the RAG Prompt for this specific turn
        current_turn_content = f"""
        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {rag_context}

        USER QUESTION:
        {user_question}
        """

        # 2. Build the Message Payload
        # [System Message] + [Previous History] + [Current Question]
        history_messages = self.translate_history(self.router.get_history())
        messages = [self.system_message] + history_messages + [
            {"role": "user", "content": current_turn_content}
        ]
        tools = [{
            "type": "function",
            "function": {
                "name": "search_RAG_database",
                "description": (
                    "Search the RAG database for relevant context. "
                    "Relevant document options: debian, proxmox, docker, general, no_rag."
                ),
                "parameters": {
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
                            "description": "Document labels to search",
                        },
                    },
                    "required": ["query", "relevant_documents"],
                },
            },
        }]
        # DEBUG: Print what we are sending
        print("\n" + ">"*20 + f" [DEBUG] SENDING TO LOCAL {self.model} " + ">"*20)
        # We print only the last message to keep logs readable, or len(messages)
        print(f"Sending {len(messages)} messages...")
        print(f"[AI Debug Print] Context chars: {len(rag_context)}")
        print("<"*20 + " END PAYLOAD " + "<"*20 + "\n")

        try:
            # 3. Call Ollama
            response = ollama.chat(model=self.model, messages=messages, tools=tools)
            message = response.get("message", {})
            tool_calls = message.get("tool_calls") or []
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
            tool_calls = normalized_tool_calls
            print(f"[TOOL DEBUG] Tool calls returned: {len(tool_calls)}")
            if tool_calls:
                print(f"[TOOL DEBUG] Raw tool_calls: {json.dumps(tool_calls, indent=2, default=str)}")

            if tool_calls:
                assistant_message = {
                    "role": "assistant",
                    "content": message.get("content", ""),
                    "tool_calls": tool_calls,
                }
                tool_messages = []
                for tool_call in tool_calls:
                    print(f"[TOOL DEBUG] Handling tool_call: {json.dumps(tool_call, indent=2, default=str)}")
                    tool_messages.append(self._build_tool_message(tool_call))
                print(f"[TOOL DEBUG] Tool messages: {json.dumps(tool_messages, indent=2, default=str)}")

                followup_messages = messages + [assistant_message] + tool_messages
                print("[TOOL DEBUG] Sending follow-up request with tool results.")
                response = ollama.chat(
                    model=self.model,
                    messages=followup_messages,
                    tools=tools,
                )
                model_response = response["message"]["content"]
            else:
                model_response = message.get("content", "")

            # 4. Update History (CLEANLY)
            # We discard the massive RAG context from history to save context window
            self.router.update_history("user", user_question)
            self.router.update_history("model", model_response)
            
            return model_response

        except Exception as e:
            return f"Ollama Error: {str(e)}"

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
            if role == "model":
                role = "assistant"
            if not role:
                continue
            translated.append({"role": role, "content": content})
        return translated

    def _build_tool_message(self, tool_call):
        function = tool_call.get("function", {})
        tool_name = function.get("name", "unknown_tool")
        tool_args = self._parse_tool_arguments(function.get("arguments", {}))

        print(f"[TOOL DEBUG] Parsed tool args for {tool_name}: {tool_args}")
        tool_result = self._run_tool(tool_name, tool_args)
        if not isinstance(tool_result, str):
            tool_result = json.dumps(tool_result)
        print(f"[TOOL DEBUG] Tool result for {tool_name}: {tool_result}")

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
        if tool_name == "search_RAG_database":
            try:
                query = tool_args.get("query")
                relevant_documents = tool_args.get("relevant_documents")
                if query is None or relevant_documents is None:
                    return "Tool error: missing required arguments"
                return self.search_rag_database(query, relevant_documents)
            except Exception as exc:
                return f"Tool error: {exc}"
        return f"Tool error: unknown tool '{tool_name}'"

    def search_rag_database(self, retrieval_query, relevant_documents):
        return self.router.getDB().retrieve_context(retrieval_query, relevant_documents)
