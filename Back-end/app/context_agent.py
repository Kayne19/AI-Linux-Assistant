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
    def call_api(self, user_question, chat_history):
        """
        Args:
            user_question (str): The new raw input from the user.
            chat_history (list): The list of dictionaries [{'role': 'user', 'content': '...'}, ...] 
                                 from your MAIN application logic.
        """
        
        # 1. GUARD CLAUSE: If history is empty, there is nothing to recontextualize.
        if not chat_history:
            return user_question

        # 2. Format the Main App's history into a string
        # We take the last 4 messages to save context tokens.
        recent_history_text = ""
        last_user_message = ""
        for msg in chat_history[-4:]:
            if isinstance(msg, tuple) and len(msg) == 2:
                raw_role, content = msg
            else:
                raw_role = msg.get("role")
                # Handle different history formats (parts vs content)
                content = msg.get("content") or msg.get("parts", [{}])[0].get("text", "")
            role = "User" if raw_role == "user" else "Model"
            recent_history_text += f"{role}: {content}\n"
            if raw_role == "user":
                last_user_message = content

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
            
            # Logging for your sanity
            print(f"\n[Contextualizer] In: '{user_question}'")  # AI Debug Print
            print(f"[Contextualizer] Out: '{rewritten}'")  # AI Debug Print
            print(f"[Contextualizer] History chars: {len(recent_history_text)}")  # AI Debug Print
            
            return rewritten

        except Exception as e:
            print(f"[Contextualizer] Error: {e}")  # AI Debug Print
            return user_question # Fallback: just return the original input
