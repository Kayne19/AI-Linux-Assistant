import ollama
import json

class LocalCaller:
    #
    # "llama3.1:8b"
    # "mannix/llama3.1-8b-abliterated"
    #
    def __init__(self, model="mannix/llama3.1-8b-abliterated"):
        self.model = model
        self.conversation_history = []
        
        # We set the system prompt once. Ollama handles it as a message role.
        self.system_message = {
            "role": "system",
            "content": """
            You are an expert Linux System Administrator.
            INSTRUCTIONS:
            1. Guide the user through installation or troubleshooting steps.
            2. Use the provided CONTEXT from the manual.
            3. If a command is dangerous (like rm -rf), add a WARNING.
            4. If the manual doesn't have the answer, use your general knowledge but mention "I am using general Linux knowledge".
            """
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
        messages = [self.system_message] + self.conversation_history + [
            {"role": "user", "content": current_turn_content}
        ]

        # DEBUG: Print what we are sending
        print("\n" + ">"*20 + f" [DEBUG] SENDING TO LOCAL {self.model} " + ">"*20)
        # We print only the last message to keep logs readable, or len(messages)
        print(f"Sending {len(messages)} messages...")
        print("<"*20 + " END PAYLOAD " + "<"*20 + "\n")

        try:
            # 3. Call Ollama
            response = ollama.chat(model=self.model, messages=messages)
            model_response = response['message']['content']

            # 4. Update History (CLEANLY)
            # We discard the massive RAG context from history to save context window
            self.conversation_history.append({
                "role": "user", 
                "content": user_question
            })
            self.conversation_history.append({
                "role": "assistant", 
                "content": model_response
            })
            
            return model_response

        except Exception as e:
            return f"Ollama Error: {str(e)}"