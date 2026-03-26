from history_preparer import PreparedHistory, prepare_history
from openAI_caller import OpenAIWorker
from prompts import CONTEXT_SUMMARIZER_SYSTEM_PROMPT, HISTORY_SUMMARIZER_SYSTEM_PROMPT


class HistorySummarizer:
    def __init__(
        self,
        worker=None,
        model="gpt-4.1-mini",
        temperature=0.1,
        max_recent_turns=4,
        summarize_turn_threshold=16,
        summarize_char_threshold=3600,
    ):
        self.worker = worker or OpenAIWorker(model=model)
        self.temperature = temperature
        self.max_recent_turns = max_recent_turns
        self.summarize_turn_threshold = summarize_turn_threshold
        self.summarize_char_threshold = summarize_char_threshold

    def call_api(self, chat_history):
        prepared = prepare_history(
            chat_history,
            persisted_summary="",
            max_recent_turns=self.max_recent_turns,
        )
        if not prepared.recent_turns and not prepared.summary_text:
            return prepared, False

        older_turns = chat_history[:-self.max_recent_turns] if len(chat_history) > self.max_recent_turns else []
        total_chars = sum(len((item[1] if isinstance(item, tuple) and len(item) == 2 else "")) for item in chat_history)
        should_summarize = bool(
            older_turns
            and (
                len(chat_history) >= self.summarize_turn_threshold
                or total_chars >= self.summarize_char_threshold
            )
        )
        if not should_summarize:
            return prepared, False

        older_text = prepare_history(
            older_turns,
            persisted_summary="",
            max_recent_turns=len(older_turns),
        ).as_prompt_text()
        user_message = f"""
        <older_conversation>
        {older_text}
        </older_conversation>
        """

        try:
            summary_text = self.worker.generate_text(
                system_prompt=HISTORY_SUMMARIZER_SYSTEM_PROMPT,
                user_message=user_message,
                history=[],
                temperature=self.temperature,
                max_output_tokens=300,
            ).strip()
        except Exception:
            summary_text = prepared.summary_text

        return (
            PreparedHistory(
                recent_turns=chat_history[-self.max_recent_turns:],
                summary_text=summary_text,
            ),
            True,
        )


class ContextSummarizer:
    def __init__(self, worker=None, model="gpt-4.1-mini", summarize_char_threshold=2200):
        self.worker = worker or OpenAIWorker(model=model)
        self.summarize_char_threshold = summarize_char_threshold

    def call_api(self, user_question, retrieved_docs):
        if not retrieved_docs.strip():
            return "", False
        if len(retrieved_docs) < self.summarize_char_threshold:
            return retrieved_docs, False

        user_message = f"""
        <user_question>
        {user_question}
        </user_question>

        <retrieved_context>
        {retrieved_docs}
        </retrieved_context>
        """

        try:
            summary = self.worker.generate_text(
                system_prompt=CONTEXT_SUMMARIZER_SYSTEM_PROMPT,
                user_message=user_message,
                history=[],
                temperature=0.1,
                max_output_tokens=360,
            ).strip()
            return summary or retrieved_docs, True
        except Exception:
            return retrieved_docs, False
