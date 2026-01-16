import requests
import os
import json
from dotenv import load_dotenv
# --- SETUP ---
class GeminiCaller:
    # gemini-3-flash-preview
    # gemma-3-27b
    def __init__(self, router, system_prompt, model="gemma-3-27b-it"):
        load_dotenv()
        self.router = router
        self.system_prompt = system_prompt
        self.API_KEY = os.getenv("GOOGLE_API_KEY")
        self.model = model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.API_KEY}"
        self.headers = {"Content-Type": "application/json"}

    def call_api(self, user_question, rag_context):
        
        current_turn_content = f"""
        SYSTEM INSTRUCTION:
        {self.system_prompt}

        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {rag_context}

        USER QUESTION:
        {user_question}
        """
        
        current_message = {
            "role": "user",
            "parts": [{"text": current_turn_content}]
        }

        payload_messages = self.router.get_history() + [current_message]
        
        data = {
            "contents": payload_messages,
            "generationConfig": {
                #"temperature": 0.1,
                "maxOutputTokens": 2048*2
            }
        }
        
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
        
        response = requests.post(self.url, headers=self.headers, json=data)
        try:
            response_json = response.json()
            if "error" in response_json:
                return f"API Error: {response_json['error']['message']}"
                
            model_response = response_json['candidates'][0]['content']['parts'][0]['text']
            
            # Update Permanent History (CLEANLY)
            # We only save the Question and the Answer. We discard the Context.
            self.router.update_history("user", user_question)
            self.router.update_history("model", model_response)
            
            
            return model_response
            
        except (KeyError, IndexError) as e:
            print("Raw Error Response:", json.dumps(response.json(), indent=2))
            return "Error: Could not parse response."
