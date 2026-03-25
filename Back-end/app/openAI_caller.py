from dotenv import load_dotenv
from openai import OpenAI


class OpenAICaller:
    def __init__(self, router, system_prompt, model="gpt-4.1-mini"):
        load_dotenv()
        self.client = OpenAI()
        self.router = router
        self.system_prompt = system_prompt
        self.model = model

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

    def call_api(self, user_query, rag_context):
        current_turn_content = f"""
        REFERENCE CONTEXT (Use this to answer, but do not memorize it):
        {rag_context}

        USER QUESTION:
        {user_query}
        """

        history_messages = self._translate_history(self.router.get_history())
        input_items = history_messages + [{"role": "user", "content": current_turn_content}]

        response = self.client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=input_items,
        )
        model_response = response.output_text or ""

        self.router.update_history("user", user_query)
        self.router.update_history("model", model_response)

        return model_response


openAICaller = OpenAICaller
