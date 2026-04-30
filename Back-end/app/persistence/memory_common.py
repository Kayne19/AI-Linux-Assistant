from dataclasses import dataclass, field
import re

from utils.time_utils import _utc_now as _utc_now_dt


DEFAULT_HOST_LABEL = "local"

PROMOTED_FACT_KEYS = [
    "os.pretty_name",
    "os.distribution",
    "os.version",
    "kernel.release",
    "kernel.architecture",
    "shell.default",
    "hardware.cpu_model",
    "hardware.cpu_cores",
    "hardware.ram_gb",
    "hardware.gpu",
    "virtualization.platform",
    "virtualization.type",
    "container.runtime",
    "package_manager.default",
    "storage.root",
    "network.hostname",
]

FACT_LABELS = {
    "container.runtime": "Container runtime",
    "hardware.cpu_cores": "CPU cores",
    "hardware.cpu_model": "CPU model",
    "hardware.gpu": "GPU",
    "hardware.ram_gb": "RAM",
    "kernel.architecture": "Architecture",
    "kernel.release": "Kernel",
    "network.hostname": "Hostname",
    "os.distribution": "Distribution",
    "os.pretty_name": "OS",
    "os.version": "OS version",
    "package_manager.default": "Package manager",
    "shell.default": "Shell",
    "storage.root": "Root filesystem",
    "virtualization.platform": "Platform",
    "virtualization.type": "Virtualization",
}


def _display_fact_label(fact_key):
    return FACT_LABELS.get(
        fact_key, fact_key.replace(".", " ").replace("_", " ").title()
    )


def _utc_now():
    return _utc_now_dt().isoformat()


def _truncate(text, limit=220):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _clean_text(text):
    return " ".join((text or "").split())


def _tokenize(text):
    return [
        token
        for token in re.split(r"[^a-zA-Z0-9_.+-]+", (text or "").lower())
        if len(token) >= 3
    ]


def _relevance_score(query_tokens, text):
    if not query_tokens:
        return 0
    haystack = (text or "").lower()
    score = 0
    for token in query_tokens:
        if token in haystack:
            score += 1
    return score


@dataclass
class MemorySnapshot:
    host_label: str = DEFAULT_HOST_LABEL
    profile_facts: list[dict] = field(default_factory=list)
    active_issues: list[dict] = field(default_factory=list)
    relevant_attempts: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    preferences: list[dict] = field(default_factory=list)
    session_summary: str = ""

    def as_prompt_text(self):
        sections = []

        if self.profile_facts:
            sections.append("KNOWN SYSTEM PROFILE:")
            for fact in self.profile_facts:
                label = _display_fact_label(fact["fact_key"])
                provenance = fact.get("source_type", "memory")
                sections.append(f"- {label}: {fact['fact_value']} ({provenance})")

        if self.active_issues:
            sections.append("KNOWN ISSUES:")
            for issue in self.active_issues:
                line = f"- [{issue.get('status', 'unknown')}] {issue.get('title', 'Untitled issue')}"
                summary = (issue.get("summary") or "").strip()
                if summary:
                    line += f": {summary}"
                sections.append(line)

        if self.relevant_attempts:
            sections.append("PREVIOUS ATTEMPTS:")
            for attempt in self.relevant_attempts:
                action = attempt.get("action", "").strip()
                command = attempt.get("command", "").strip()
                outcome = (
                    attempt.get("outcome", "").strip()
                    or attempt.get("status", "").strip()
                )
                parts = [part for part in [action, command, outcome] if part]
                if parts:
                    sections.append("- " + " | ".join(parts))

        if self.constraints:
            sections.append("KNOWN CONSTRAINTS:")
            for constraint in self.constraints:
                value = constraint.get("constraint_value", "").strip()
                if value:
                    sections.append(f"- {value}")

        if self.preferences:
            sections.append("KNOWN PREFERENCES:")
            for preference in self.preferences:
                value = preference.get("preference_value", "").strip()
                if value:
                    sections.append(f"- {value}")

        if self.session_summary.strip():
            sections.append("SESSION NOTES:")
            sections.append(self.session_summary.strip())

        return "\n".join(sections).strip()
