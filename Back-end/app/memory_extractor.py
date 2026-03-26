import json
import re

from prompts import MEMORY_EXTRACTOR_SYSTEM_PROMPT


def _truncate(text, limit=220):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_key(value):
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_.")
    return value


def _normalize_source_type(value, default="model"):
    value = (value or "").strip().lower()
    if value in {"user", "assistant", "model"}:
        return value
    return default


def _default_source_ref(source_type):
    if source_type == "user":
        return "user_question"
    if source_type == "assistant":
        return "assistant_response"
    return "conversation"


def _extract_json_object(text):
    text = (text or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class MemoryExtractor:
    def __init__(
        self,
        worker,
        system_prompt=MEMORY_EXTRACTOR_SYSTEM_PROMPT,
        max_output_tokens=700,
    ):
        if worker is None:
            raise ValueError("MemoryExtractor requires an injected worker instance.")
        self.worker = worker
        self.system_prompt = system_prompt
        self.max_output_tokens = max_output_tokens

    def call_api(self, user_question, assistant_response):
        payload = {
            "user_question": user_question or "",
            "assistant_response": assistant_response or "",
        }
        try:
            response = self.worker.generate_text(
                system_prompt=self.system_prompt,
                user_message=json.dumps(payload, indent=2),
                history=[],
                temperature=0.1,
                max_output_tokens=self.max_output_tokens,
            )
        except Exception:
            return self.empty_result()

        parsed = _extract_json_object(response)
        if not isinstance(parsed, dict):
            return self.empty_result()
        return self._normalize_result(parsed)

    def empty_result(self):
        return {
            "facts": [],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
            "session_summary": "",
        }

    def _normalize_result(self, parsed):
        result = self.empty_result()
        result["facts"] = self._normalize_facts(parsed.get("facts", []))
        result["issues"] = self._normalize_issues(parsed.get("issues", []))
        result["attempts"] = self._normalize_attempts(parsed.get("attempts", []))
        result["constraints"] = self._normalize_constraints(parsed.get("constraints", []))
        result["preferences"] = self._normalize_preferences(parsed.get("preferences", []))
        result["session_summary"] = _truncate(parsed.get("session_summary", ""), limit=500)
        return result

    def _normalize_facts(self, items):
        normalized = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            fact_key = _normalize_key(item.get("fact_key"))
            fact_value = _truncate(item.get("fact_value", ""), limit=160)
            if not fact_key or not fact_value:
                continue
            if fact_key in seen:
                continue
            seen.add(fact_key)
            confidence = self._normalize_confidence(item.get("confidence"))
            source_type = _normalize_source_type(item.get("source_type"), default="user")
            normalized.append(
                {
                    "fact_key": fact_key,
                    "fact_value": fact_value,
                    "source_type": source_type,
                    "source_ref": _truncate(item.get("source_ref", _default_source_ref(source_type)), limit=80)
                    or _default_source_ref(source_type),
                    "confidence": confidence,
                    "verified": False,
                }
            )
        return normalized

    def _normalize_issues(self, items):
        normalized = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            title = _truncate(item.get("title", ""), limit=120)
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            status = (item.get("status") or "unknown").strip().lower()
            if status not in {"open", "resolved", "unknown"}:
                status = "unknown"
            source_type = _normalize_source_type(item.get("source_type"), default="user")
            normalized.append(
                {
                    "title": title,
                    "category": _normalize_key(item.get("category")) or "general",
                    "summary": _truncate(item.get("summary", ""), limit=260),
                    "status": status,
                    "source_type": source_type,
                    "source_ref": _truncate(item.get("source_ref", _default_source_ref(source_type)), limit=80)
                    or _default_source_ref(source_type),
                    "confidence": self._normalize_confidence(item.get("confidence")),
                }
            )
        return normalized

    def _normalize_attempts(self, items):
        normalized = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            action = _truncate(item.get("action", ""), limit=180)
            command = _truncate(item.get("command", ""), limit=180)
            outcome = _truncate(item.get("outcome", ""), limit=180)
            if not action and not command:
                continue
            key = (action.lower(), command.lower(), outcome.lower())
            if key in seen:
                continue
            seen.add(key)
            status = (item.get("status") or "unknown").strip().lower()
            if status not in {"attempted", "worked", "failed", "unknown"}:
                status = "unknown"
            source_type = _normalize_source_type(item.get("source_type"), default="user")
            normalized.append(
                {
                    "action": action,
                    "command": command,
                    "outcome": outcome,
                    "status": status,
                    "issue_title": _truncate(item.get("issue_title", ""), limit=120),
                    "source_type": source_type,
                    "source_ref": _truncate(item.get("source_ref", _default_source_ref(source_type)), limit=80)
                    or _default_source_ref(source_type),
                    "confidence": self._normalize_confidence(item.get("confidence")),
                }
            )
        return normalized

    def _normalize_constraints(self, items):
        normalized = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            constraint_key = _normalize_key(item.get("constraint_key"))
            constraint_value = _truncate(item.get("constraint_value", ""), limit=160)
            if not constraint_key or not constraint_value:
                continue
            key = (constraint_key, constraint_value.lower())
            if key in seen:
                continue
            seen.add(key)
            source_type = _normalize_source_type(item.get("source_type"), default="user")
            normalized.append(
                {
                    "constraint_key": constraint_key,
                    "constraint_value": constraint_value,
                    "source_type": source_type,
                    "source_ref": _truncate(item.get("source_ref", _default_source_ref(source_type)), limit=80)
                    or _default_source_ref(source_type),
                    "confidence": self._normalize_confidence(item.get("confidence")),
                }
            )
        return normalized

    def _normalize_preferences(self, items):
        normalized = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            preference_key = _normalize_key(item.get("preference_key"))
            preference_value = _truncate(item.get("preference_value", ""), limit=160)
            if not preference_key or not preference_value:
                continue
            key = (preference_key, preference_value.lower())
            if key in seen:
                continue
            seen.add(key)
            source_type = _normalize_source_type(item.get("source_type"), default="user")
            normalized.append(
                {
                    "preference_key": preference_key,
                    "preference_value": preference_value,
                    "source_type": source_type,
                    "source_ref": _truncate(item.get("source_ref", _default_source_ref(source_type)), limit=80)
                    or _default_source_ref(source_type),
                    "confidence": self._normalize_confidence(item.get("confidence")),
                }
            )
        return normalized

    def _normalize_confidence(self, value):
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(confidence, 1.0))
