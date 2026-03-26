from openAI_caller import OpenAIWorker
from debug_utils import debug_print
from history_preparer import PreparedHistory, summarize_turns
from prompts import CONTEXTUALIZER_SYSTEM_PROMPT

class Contextualizer:
    # qwen2.5:7b
    # "mannix/llama3.1-8b-abliterated"
    # "mistral-nemo"
    def __init__(self, worker=None, model="gpt-4.1-mini", temperature=0.0):
        self.worker = worker or OpenAIWorker(model=model)
        self.system_prompt = CONTEXTUALIZER_SYSTEM_PROMPT
        self.temperature = temperature

    def call_api(self, user_question, recent_turns=None):
        """
        Args:
            user_question (str): The new raw input from the user.
            recent_turns: Recent raw conversation turns for local reference resolution.
        """

        if recent_turns is None:
            recent_turns = []
        elif isinstance(recent_turns, PreparedHistory):
            recent_turns = recent_turns.recent_turns

        if not recent_turns:
            return user_question

        recent_history_text = summarize_turns(
            recent_turns,
            max_entries=len(recent_turns),
        )

        user_message_content = f"""
        <task_data>
        <recent_turns>
        {recent_history_text}
        </recent_turns>
        <current_query>
        {user_question}
        </current_query>
        </task_data>
        """

        try:
            rewritten = self.worker.generate_text(
                system_prompt=self.system_prompt,
                user_message=user_message_content,
                history=[],
                temperature=self.temperature,
            )
            rewritten = rewritten.strip()
            
            debug_print(f"\n[Contextualizer] In: '{user_question}'")
            debug_print(f"[Contextualizer] Out: '{rewritten}'")
            debug_print(f"[Contextualizer] History chars: {len(recent_history_text)}")
            
            return rewritten

        except Exception as e:
            debug_print(f"[Contextualizer] Error: {e}")
            return user_question # Fallback: just return the original input
