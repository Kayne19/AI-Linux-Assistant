from openAI_caller import OpenAIWorker
from debug_utils import debug_print
from history_preparer import PreparedHistory
from prompts import build_classifier_system_prompt
from routing_registry import get_allowed_labels, get_domain_map

class Classifier:
    def __init__(self, worker=None, model="gpt-4.1-mini", temperature=0.0):
        self.worker = worker or OpenAIWorker(model=model)
        self.temperature = temperature

    def _build_system_prompt(self):
        allowed_labels = get_allowed_labels()
        domain_map = get_domain_map()
        return build_classifier_system_prompt(allowed_labels, domain_map)

    # Parse classifier output into a list of allowed labels.
    def _parse_labels(self, output):
        if not output:
            return []
        line = output.strip().splitlines()[0]
        label_part = None
        for part in line.split(","):
            part = part.strip()
            if part.startswith("labels="):
                label_part = part[len("labels="):].strip()
                break
        if label_part is None:
            return []
        labels = [label.strip() for label in label_part.split("|") if label.strip()]
        allowed = set(get_allowed_labels())
        labels = [label for label in labels if label in allowed]
        return labels

    def call_api(self, user_question, summarized_conversation_history=None, memory_snapshot_text=""):
        """
        Args:
            user_question (str): The new raw input from the user.
            summarized_conversation_history: Prepared history object for the current turn.
        """

        if summarized_conversation_history is None:
            summarized_conversation_history = PreparedHistory()
        recent_history_text = summarized_conversation_history.as_prompt_text()

        # 3. Construct the Data Payload (The Prompt)
        # We wrap the data in XML tags so the model knows it is data, not chat.
        user_message_content = f"""
        <task_data>
        <memory>
        {memory_snapshot_text}
        </memory>
        <history>
        {recent_history_text}
        </history>
        <current_query>
        {user_question}
        </current_query>
        </task_data>
        """

        # 4. Build Messages for Ollama
        try:
            output = self.worker.generate_text(
                system_prompt=self._build_system_prompt(),
                user_message=user_message_content,
                history=[],
                temperature=self.temperature,
            )
            output = output.strip()
            labels = self._parse_labels(output)
            
            debug_print(f"\n[Classifier] In: '{user_question}'")
            debug_print(f"[Classifier] Out: '{output}'")
            debug_print(f"[Classifier] History chars: {len(recent_history_text)}")
            
            return labels

        except Exception as e:
            debug_print(f"[Classifier] Error: {e}")
            return [] # Fallback: no sources
