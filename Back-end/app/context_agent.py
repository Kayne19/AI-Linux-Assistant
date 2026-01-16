import string

import ollama

class Contextualizer:
    # qwen2.5:7b
    # "mannix/llama3.1-8b-abliterated"
    # "mistral-nemo"
    def __init__(self, model="qwen2.5:7b"):
        self.model = model
        
        # XML-based System Prompt (The "Nuclear" Option)
        # This structure prevents the model from getting confused 
        # between instructions and conversation.
        self.system_prompt = """
        SYSTEM: Contextualizer (Pronoun Resolver)

        Task:
        Rewrite the latest USER message into a standalone message by resolving pronouns/ellipsis using HISTORY.
        Do not answer. Do not summarize. Do not add extra content.
        Only replace pronouns/ellipsis with exact text copied from HISTORY.
        Do not change casing, punctuation, or verb tense. Do not add determiners.
        Never append or quote HISTORY beyond the exact replacement text.
        If HISTORY is empty, return the USER message unchanged.
        If the USER message has no pronouns/ellipsis to resolve, return it verbatim.

        INPUTS YOU WILL RECEIVE (verbatim):
        HISTORY: <recent conversation turns, may be empty>
        USER: <latest user message>

        OUTPUT (STRICT):
        Return ONLY the rewritten USER message text.
        No labels. No explanations. No JSON. No extra lines.
        Do not include any history content in the output, even paraphrased.

        HARD RULES:
        - Keep the USER message as close as possible to the original wording.
        - Only change what is necessary to make references explicit.
        - Resolve pronouns and vague references using HISTORY:
          it, this, that, they, them, there, he, she, him, her, those, these
        - Replace pronouns with the most recent specific noun phrase in HISTORY that matches.
        - Replace “there” with the most recent specific location/path/URL in HISTORY (if any).
        - If the referent is unknown or ambiguous, leave the pronoun as-is (do NOT guess).
        - Preserve the user’s intent and sentence type (question stays a question).
        - Preserve any pasted logs/code verbatim. Do not trim. Do not reformat.
        - Do not invent specifics (brands, commands, errors, versions) that were not present.
        - After rewriting, every word must already exist in USER or HISTORY (copy-paste only).

        RESOLUTION HEURISTIC:
        - Prefer the most recent concrete noun phrase (proper names, product names, technical objects).
        - If multiple candidates exist, do not guess; keep the original pronoun unchanged.
        - If the USER message contains multiple lines (logs), only rewrite the first line.

        EXAMPLES (follow exactly):

        HISTORY: User: "I want a ferrari"
        USER: "How much is it?"
        OUTPUT: "How much is a ferrari?"

        HISTORY: User: "I'm looking at a used 2019 Honda Civic and a 2020 Corolla"
        USER: "Which one is cheaper?"
        OUTPUT: "Which one is cheaper?"

        HISTORY: User: "I'm trying to create a Debian container in Proxmox"
        USER: "what is the command to install it on my drive?"
        OUTPUT: "what is the command to install the Debian container in Proxmox on my drive?"

        HISTORY: User: "The installer logs are in /var/log/syslog"
        USER: "How do I view them there?"
        OUTPUT: "How do I view the installer logs in /var/log/syslog?"

        HISTORY: (empty)
        USER: "How much is it?"
        OUTPUT: "How much is it?"

        HISTORY: (empty)
        USER: "How do I shut them all off?"
        OUTPUT: "How do I shut them all off?"

        HISTORY: User: "Error: 'permission denied' when running apt update"
        USER: "How do I fix it?\n<100 lines of log...>"
        OUTPUT: "How do I fix the 'permission denied' error when running apt update?\n<100 lines of log...>"

        """
        self._strip_chars = string.punctuation
        self._pronouns = {
            "it",
            "this",
            "that",
            "they",
            "them",
            "there",
            "he",
            "she",
            "him",
            "her",
            "those",
            "these",
        }
        self._singular_pronouns = {
            "it",
            "this",
            "that",
            "he",
            "she",
            "him",
            "her",
        }
        self._narrative_tokens = {
            "now",
            "after",
            "before",
            "because",
            "since",
            "so",
            "then",
        }
        self._stopwords = {
            "a",
            "an",
            "the",
            "and",
            "or",
            "to",
            "of",
            "for",
            "in",
            "on",
            "at",
            "with",
            "my",
            "your",
        }

    # Split text into lowercase word tokens, trimming punctuation.
    def _tokenize(self, text):
        tokens = []
        for raw in text.split():
            token = raw.strip(self._strip_chars).lower()
            if token:
                tokens.append(token)
        return tokens

    # Quick check for any pronoun in the input text.
    def _has_pronoun(self, text):
        for token in self._tokenize(text):
            if token in self._pronouns:
                return True
        return False

    # Check only singular pronouns to avoid ambiguous multi-entity rewrites.
    def _has_singular_pronoun(self, text):
        for token in self._tokenize(text):
            if token in self._singular_pronouns:
                return True
        return False

    # Detect likely ambiguity when prior user message mentions multiple entities.
    def _looks_ambiguous(self, text):
        if not text:
            return False
        lowered = f" {text.lower()} "
        if " and " not in lowered and " or " not in lowered:
            return False
        for token in self._narrative_tokens:
            if f" {token} " in lowered:
                return False
        return True

    # Append prior user context when a pronoun remains after rewriting.
    def _append_context(self, query, context):
        if not context or context.strip() in query:
            return query
        if "\n" in context or "\n" in query:
            return query
        context = context.strip()
        if len(context) > 200:
            context = context[:200].rsplit(" ", 1)[0]
        return f"{query} {context}".strip()

    # Reject rewrites that introduce new tokens or alter log lines.
    def _validate_rewrite(self, original, candidate, history_text):
        if not candidate:
            return False
        if "\n" in original:
            orig_lines = original.splitlines()
            cand_lines = candidate.splitlines()
            if len(cand_lines) < len(orig_lines):
                return False
            if cand_lines[1:] != orig_lines[1:]:
                return False
        allowed = set(self._tokenize(original)) | set(self._tokenize(history_text)) | self._stopwords
        extra = set(self._tokenize(candidate)) - allowed
        if extra:
            return False
        return True

    def call_api(self, user_question, chat_history):
        """
        Args:
            user_question (str): The new raw input from the user.
            chat_history (list): The list of dictionaries [{'role': 'user', 'content': '...'}, ...] 
                                 from your MAIN application logic.
        """
        
        # 1. GUARD CLAUSE: If history is empty, there is nothing to recontextualize.
        # if not chat_history:
            # return user_question

        # 2. Format the Main App's history into a string
        # We take the last 4 messages to save context tokens.
        recent_history_text = ""
        last_user_message = ""
        for msg in chat_history[-4:]: 
            role = "User" if msg['role'] == 'user' else "Model"
            # Handle different history formats (parts vs content)
            content = msg.get('content') or msg.get('parts', [{}])[0].get('text', "")
            recent_history_text += f"{role}: {content}\n"
            if msg.get('role') == 'user':
                last_user_message = content

        if not chat_history or not self._has_pronoun(user_question):
            return user_question
        if self._looks_ambiguous(last_user_message) and self._has_singular_pronoun(user_question):
            return user_question

        # 3. Construct the Data Payload (The Prompt)
        # We wrap the data in XML tags so the model knows it is data, not chat.
        user_message_content = f"""
        <task_data>
        <history>
        {recent_history_text}
        </history>
        <current_query>
        {user_question}
        </current_query>
        </task_data>
        """

        # 4. Build Messages for Ollama
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message_content}
        ]

        try:
            # 5. Call Ollama with low temperature
            response = ollama.chat(
                model=self.model, 
                messages=messages,
                options={'temperature': 0.1} # Crucial: makes output deterministic
            )
            
            rewritten = response['message']['content'].strip()
            if not self._validate_rewrite(user_question, rewritten, recent_history_text):
                rewritten = user_question
            if self._has_pronoun(rewritten) and not self._looks_ambiguous(last_user_message):
                rewritten = self._append_context(rewritten, last_user_message)
            
            # Logging for your sanity
            print(f"\n[Contextualizer] In: '{user_question}'")  # AI Debug Print
            print(f"[Contextualizer] Out: '{rewritten}'")  # AI Debug Print
            print(f"[Contextualizer] History chars: {len(recent_history_text)}")  # AI Debug Print
            
            return rewritten

        except Exception as e:
            print(f"[Contextualizer] Error: {e}")  # AI Debug Print
            return user_question # Fallback: just return the original input
