from dataclasses import dataclass, field


def _message_content(item):
    if isinstance(item, tuple) and len(item) == 2:
        return item[0], item[1]
    if isinstance(item, dict):
        role = item.get("role")
        content = item.get("content") or item.get("parts", [{}])[0].get("text", "")
        return role, content
    return None, ""


def _display_role(raw_role):
    if raw_role == "user":
        return "User"
    return "Model"


def _clean_line(text, limit=200):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def summarize_turns(history, max_entries=8, allowed_roles=None):
    lines = []
    for item in history[-max_entries:]:
        raw_role, content = _message_content(item)
        if not raw_role or not content:
            continue
        if allowed_roles is not None and raw_role not in allowed_roles:
            continue
        lines.append(f"{_display_role(raw_role)}: {_clean_line(content)}")
    return "\n".join(lines)


@dataclass
class PreparedHistory:
    recent_turns: list = field(default_factory=list)
    summary_text: str = ""

    def as_prompt_text(self):
        parts = []
        if self.summary_text:
            parts.append(self.summary_text)
        recent = summarize_turns(self.recent_turns, max_entries=len(self.recent_turns))
        if recent:
            parts.append("Recent turns:\n" + recent)
        return "\n\n".join(part for part in parts if part).strip()


def prepare_history(history, persisted_summary="", max_recent_turns=6):
    if not history:
        return PreparedHistory(summary_text=(persisted_summary or "").strip())

    older_turns = history[:-max_recent_turns] if len(history) > max_recent_turns else []
    summary_parts = []
    if persisted_summary:
        summary_parts.append("Persistent session summary:\n" + persisted_summary.strip())
    if older_turns:
        older_summary = summarize_turns(older_turns, max_entries=10)
        if older_summary:
            summary_parts.append("Older conversation summary:\n" + older_summary)

    return PreparedHistory(
        recent_turns=history[-max_recent_turns:],
        summary_text="\n\n".join(summary_parts).strip(),
    )
