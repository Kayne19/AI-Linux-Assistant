from dotenv import load_dotenv
import requests
import os

# Set your API key (or put it in an env var GOOGLE_API_KEY)
load_dotenv()  # returns True if a file was found
API_KEY = os.getenv("GOOGLE_API_KEY")
MODEL = "gemini-2.5-flash-lite"

url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={API_KEY}"
headers = {"Content-Type": "application/json"}
data = {
    "contents": [
        {
            "parts": [
                {"text": "Explain how AI works in a few words"}
            ]
        }
    ]
}
response = requests.post(url, headers=headers, json=data)
def call_gemini_api(prompt):
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    response = requests.post(url, headers=headers, json=data)

    # Full JSON
    print("Raw response:\n", response.json())

    # Just the text
    candidates = response.json().get("candidates", [])
    if candidates:
        text = candidates[0]["content"]["parts"][0]["text"]
        return text
    else:
        return "No candidates in response"
while(True):
    example_prompt = input("What do you want to ask Gemini? Say exit to quit: \n" )
    if example_prompt.lower() == "exit":
        break
    print(call_gemini_api(example_prompt))
    print("\n")
    




