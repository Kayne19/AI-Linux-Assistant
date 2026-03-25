import ollama

class Classifier:
    def __init__(self, model="qwen2.5:7b"):
        self.model = model
        self.system_prompt = """

    You are a routing classifier for RAG.
    Goal: choose which document domains to search for the user’s message.

    Return EXACTLY one line in this format:
    labels=LABELS,conf=0.00

    Rules:

    - Allowed labels: debian, proxmox, docker, general, no_rag
    - Multiple labels must be joined with | in this fixed order: debian|proxmox|docker|general|no_rag
    - Confidence is a number from 0.00 to 1.00 with two decimals.
    - Output only the line. No extra words, no quotes, no spaces.

    Routing guidance:

    - no_rag: greetings, thanks, small talk, meta questions (e.g., “hello”, “thanks”, “who are you”).
    - debian: Debian install, apt, dpkg, Debian installer, Debian versions.
    - proxmox: Proxmox/PVE, VM/LXC management, nodes, clusters, storage, backups, Proxmox UI.
    - docker: Docker, containers/images, Dockerfile, docker compose, docker CLI.
    - general: generic Linux shell/filesystem questions without a clear distro/platform.
    - If the query clearly spans multiple domains, output multiple labels (e.g., “Debian container in
        Proxmox” → debian|proxmox).
    - If uncertain between two or more domains, output all plausible labels with lower confidence (≤0.60).

    Examples (follow format exactly):
    labels=no_rag,conf=1.00
    labels=debian,conf=0.92
    labels=proxmox|debian,conf=0.85
    labels=docker,conf=0.90
    labels=general,conf=0

    """

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
        allowed = {"debian", "proxmox", "docker", "general", "no_rag"}
        labels = [label for label in labels if label in allowed]
        return labels

    def call_api(self, user_question, chat_history):
        """
        Args:
            user_question (str): The new raw input from the user.
            chat_history (list): The list of dictionaries [{'role': 'user', 'content': '...'}, ...] 
                                    from your MAIN application logic.
        """
        

        # 1. Format the Main App's history into a string
        recent_history_text = ""
        for msg in chat_history[-12:]:
            if isinstance(msg, tuple) and len(msg) == 2:
                raw_role, content = msg
            else:
                raw_role = msg.get("role")
                # Handle different history formats (parts vs content)
                content = msg.get("content") or msg.get("parts", [{}])[0].get("text", "")
            role = "User" if raw_role == "user" else "Model"
            recent_history_text += f"{role}: {content}\n"

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
            
            output = response['message']['content'].strip()
            labels = self._parse_labels(output)
            
            # Logging
            print(f"\n[Classifier] In: '{user_question}'")  # AI Debug Print
            print(f"[Classifier] Out: '{output}'")  # AI Debug Print
            print(f"[Classifier] History chars: {len(recent_history_text)}")  # AI Debug Print
            
            return labels

        except Exception as e:
            print(f"[Classifier] Error: {e}")  # AI Debug Print
            return [] # Fallback: no sources
