import requests
import os
import json
from dotenv import load_dotenv
# --- SETUP ---
class modelRouter:
    def __init__(self):
        self.conversation_history = []

    def call_gemini_api(self, user_question, rag_context):
        
        system_prompt = {
            "role": "user",
            "parts": [{"text": """
            SYSTEM: You are an expert Linux System Administrator.
                INSTRUCTIONS:
                1. Guide the user through installation or troubleshooting steps.
                2. Use the provided CONTEXT from the manual.
                3. If a command is dangerous (like rm -rf), add a WARNING.
                4. If the manual doesn't have the answer, use your general knowledge but mention "I am using general Linux knowledge".
            """}]
        }

        current_turn_content = f"""
        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {rag_context}

        USER QUESTION:
        {user_question}
        """
        
        current_message = {
            "role": "user",
            "parts": [{"text": current_turn_content}]
        }

        payload_messages = [system_prompt] + self.conversation_history + [current_message]
        
        data = {
            "contents": payload_messages,
            "generationConfig": {
                #"temperature": 0.1,
                "maxOutputTokens": 2048*2
            }
        }
        
        
        # DEBUG: SEE EXACTLY WHAT YOU ARE SENDING
        print("\n" + ">"*20 + " [DEBUG] RAW OUTGOING PAYLOAD " + ">"*20)
        print(json.dumps(data, indent=2))
        print("<"*20 + " END PAYLOAD " + "<"*20 + "\n")
        '''
        # DEBUG: SEE EXACTLY WHAT YOU GOT BACK
        try:
            debug_response = response.json()
            print("\n" + ">"*20 + " [DEBUG] RAW INCOMING RESPONSE " + ">"*20)
            print(json.dumps(debug_response, indent=2))
            print("<"*20 + " END RESPONSE " + "<"*20 + "\n")
        except:
            print(f"\n❌ [DEBUG] RAW RESPONSE TEXT (Non-JSON): {response.text}")
        '''
        
        response = requests.post(self.url, headers=self.headers, json=data)
        try:
            response_json = response.json()
            if "error" in response_json:
                return f"API Error: {response_json['error']['message']}"
                
            model_response = response_json['candidates'][0]['content']['parts'][0]['text']
            
            # Update Permanent History (CLEANLY)
            # We only save the Question and the Answer. We discard the Context.
            self.conversation_history.append({
                "role": "user", 
                "parts": [{"text": user_question}]
            })
            self.conversation_history.append({
                "role": "model", 
                "parts": [{"text": model_response}]
            })
            
            return model_response
            
        except (KeyError, IndexError) as e:
            print("Raw Error Response:", json.dumps(response.json(), indent=2))
            return "Error: Could not parse response."