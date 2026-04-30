from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MUTABLE_FACT_KEYS = {
    "os.distribution",
    "os.version",
    "os.pretty_name",
    "shell.default",
    "network.hostname",
    "environment.container",
    "environment.virtualization",
    "environment.virtualization_type",
    "environment.virtual_machine",
    "container.type",
    "container.lxc.privileged_status",
}

MUTABLE_FACT_PREFIXES = (
    "environment.",
    "container.",
    "virtualization.",
)


def _truncate(text, limit=120):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _candidate_entry(item_type, item, reason, status="candidate"):
    return {
        "item_type": item_type,
        "item_key": _item_key(item_type, item),
        "payload": dict(item),
        "status": status,
        "reason": reason,
        "confidence": float(item.get("confidence", 0.5) or 0.5),
        "source_type": item.get("source_type", "model"),
        "source_ref": item.get("source_ref", "conversation"),
    }


def _item_key(item_type, item):
    if item_type == "fact":
        return item.get("fact_key", "")
    if item_type == "issue":
        return (item.get("title") or "").strip().lower()
    if item_type == "attempt":
        return "|".join(
            [
                (item.get("action") or "").strip().lower(),
                (item.get("command") or "").strip().lower(),
                (item.get("outcome") or "").strip().lower(),
            ]
        )
    if item_type == "constraint":
        return "|".join(
            [
                (item.get("constraint_key") or "").strip().lower(),
                (item.get("constraint_value") or "").strip().lower(),
            ]
        )
    if item_type == "preference":
        return "|".join(
            [
                (item.get("preference_key") or "").strip().lower(),
                (item.get("preference_value") or "").strip().lower(),
            ]
        )
    return ""


@dataclass
class MemoryResolution:
    committed: dict = field(
        default_factory=lambda: {
            "facts": [],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
        }
    )
    candidates: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    session_summary: str = ""

    def stats(self):
        committed_stats = {key: len(value) for key, value in self.committed.items()}
        return {
            "committed": committed_stats,
            "candidates": len(self.candidates),
            "conflicts": len(self.conflicts),
            "has_session_summary": bool((self.session_summary or "").strip()),
        }

    def details(self, max_items=3):
        return {
            **self.stats(),
            "committed_examples": {
                key: [
                    _preview_item(key[:-1] if key.endswith("s") else key, item)
                    for item in value[:max_items]
                ]
                for key, value in self.committed.items()
            },
            "candidate_examples": [
                _preview_candidate(item) for item in self.candidates[:max_items]
            ],
            "conflict_examples": [
                _preview_candidate(item) for item in self.conflicts[:max_items]
            ],
        }


def _preview_item(item_type, item):
    if item_type == "fact":
        return f"{item.get('fact_key', '')}={item.get('fact_value', '')} [{item.get('source_type', 'model')}]"
    if item_type == "issue":
        return f"{item.get('title', '')} [{item.get('status', 'unknown')}]"
    if item_type == "attempt":
        parts = [
            item.get("action", ""),
            item.get("command", ""),
            item.get("outcome", ""),
        ]
        return _truncate(" | ".join(part for part in parts if part))
    if item_type == "constraint":
        return f"{item.get('constraint_key', '')}={item.get('constraint_value', '')}"
    if item_type == "preference":
        return f"{item.get('preference_key', '')}={item.get('preference_value', '')}"
    return _truncate(str(item))


def _preview_candidate(item):
    payload = item.get("payload", {})
    item_type = item.get("item_type", "unknown")
    return {
        "item_type": item_type,
        "status": item.get("status", "candidate"),
        "reason": item.get("reason", ""),
        "summary": _preview_item(item_type, payload),
    }


class MemoryResolver:
    def __init__(
        self,
        fact_commit_confidence=0.75,
        issue_commit_confidence=0.7,
        require_user_source_for_facts=True,
        require_user_source_for_preferences=True,
        require_user_source_for_constraints=True,
        require_user_source_for_attempts=True,
        conflict_staleness_days=None,
    ):
        self.fact_commit_confidence = fact_commit_confidence
        self.issue_commit_confidence = issue_commit_confidence
        self.require_user_source_for_facts = require_user_source_for_facts
        self.require_user_source_for_preferences = require_user_source_for_preferences
        self.require_user_source_for_constraints = require_user_source_for_constraints
        self.require_user_source_for_attempts = require_user_source_for_attempts
        self.conflict_staleness_days = conflict_staleness_days

    def _fact_is_mutable(self, fact_key):
        if fact_key in MUTABLE_FACT_KEYS:
            return True
        return any(fact_key.startswith(prefix) for prefix in MUTABLE_FACT_PREFIXES)

    def resolve(self, extracted, snapshot=None):
        extracted = extracted or {}
        snapshot = snapshot or {}
        resolution = MemoryResolution(
            session_summary=extracted.get("session_summary", "")
        )
        profile = snapshot.get("profile", {})
        fact_timestamps = snapshot.get("fact_timestamps", {})

        for fact in extracted.get("facts", []):
            self._resolve_fact(fact, profile, fact_timestamps, resolution)

        for issue in extracted.get("issues", []):
            self._resolve_issue(issue, resolution)

        for attempt in extracted.get("attempts", []):
            self._resolve_attempt(attempt, resolution)

        for constraint in extracted.get("constraints", []):
            self._resolve_constraint(constraint, resolution)

        for preference in extracted.get("preferences", []):
            self._resolve_preference(preference, resolution)

        return resolution

    def _resolve_fact(self, fact, profile, fact_timestamps, resolution):
        key = fact.get("fact_key", "")
        value = fact.get("fact_value", "")
        if not key or not value:
            return
        source_type = fact.get("source_type", "model")
        confidence = float(fact.get("confidence", 0.5) or 0.5)
        existing_value = profile.get(key)

        if existing_value and existing_value != value:
            if (
                self._fact_is_mutable(key)
                and source_type == "user"
                and confidence >= self.fact_commit_confidence
            ):
                resolution.conflicts.append(
                    _candidate_entry(
                        "fact",
                        {
                            "fact_key": key,
                            "fact_value": existing_value,
                            "source_type": "memory",
                            "source_ref": "committed_memory",
                            "confidence": 1.0,
                            "replaced_by": value,
                        },
                        reason=f"superseded_by_user_update:{value}",
                        status="superseded",
                    )
                )
                resolution.committed["facts"].append(fact)
                return
            if (
                self._fact_is_mutable(key)
                and self.conflict_staleness_days is not None
                and confidence >= self.fact_commit_confidence
            ):
                # Only auto-resolve if the existing fact is older than conflict_staleness_days
                existing_ts = fact_timestamps.get(key, "") if fact_timestamps else ""
                is_stale = False
                if existing_ts:
                    try:
                        fact_dt = datetime.fromisoformat(existing_ts)
                        cutoff = datetime.now(timezone.utc) - timedelta(
                            days=self.conflict_staleness_days
                        )
                        is_stale = fact_dt < cutoff
                    except (ValueError, TypeError):
                        pass
                if not is_stale:
                    # Not stale enough; surface as conflict instead
                    resolution.conflicts.append(
                        _candidate_entry(
                            "fact",
                            fact,
                            reason=f"conflicts_with_existing:{existing_value}",
                            status="conflicted",
                        )
                    )
                    return
                resolution.conflicts.append(
                    _candidate_entry(
                        "fact",
                        {
                            "fact_key": key,
                            "fact_value": existing_value,
                            "source_type": "memory",
                            "source_ref": "committed_memory",
                            "confidence": 1.0,
                            "replaced_by": value,
                        },
                        reason=f"auto_resolved_stale_conflict:{value}",
                        status="superseded",
                    )
                )
                resolution.committed["facts"].append(fact)
                return
            resolution.conflicts.append(
                _candidate_entry(
                    "fact",
                    fact,
                    reason=f"conflicts_with_existing:{existing_value}",
                    status="conflicted",
                )
            )
            return

        if self.require_user_source_for_facts and source_type != "user":
            resolution.candidates.append(
                _candidate_entry("fact", fact, reason="non_user_source")
            )
            return

        if confidence < self.fact_commit_confidence:
            resolution.candidates.append(
                _candidate_entry("fact", fact, reason="low_confidence")
            )
            return

        resolution.committed["facts"].append(fact)

    def _resolve_issue(self, issue, resolution):
        title = (issue.get("title") or "").strip()
        if not title:
            return
        confidence = float(issue.get("confidence", 0.5) or 0.5)
        if confidence < self.issue_commit_confidence:
            resolution.candidates.append(
                _candidate_entry("issue", issue, reason="low_confidence")
            )
            return
        resolution.committed["issues"].append(issue)

    def _resolve_attempt(self, attempt, resolution):
        if not (attempt.get("action") or attempt.get("command")):
            return
        if (
            self.require_user_source_for_attempts
            and attempt.get("source_type", "model") != "user"
        ):
            resolution.candidates.append(
                _candidate_entry("attempt", attempt, reason="non_user_source")
            )
            return
        resolution.committed["attempts"].append(attempt)

    def _resolve_constraint(self, constraint, resolution):
        if not (
            constraint.get("constraint_key") and constraint.get("constraint_value")
        ):
            return
        if (
            self.require_user_source_for_constraints
            and constraint.get("source_type", "model") != "user"
        ):
            resolution.candidates.append(
                _candidate_entry("constraint", constraint, reason="non_user_source")
            )
            return
        resolution.committed["constraints"].append(constraint)

    def _resolve_preference(self, preference, resolution):
        if not (
            preference.get("preference_key") and preference.get("preference_value")
        ):
            return
        if (
            self.require_user_source_for_preferences
            and preference.get("source_type", "model") != "user"
        ):
            resolution.candidates.append(
                _candidate_entry("preference", preference, reason="non_user_source")
            )
            return
        resolution.committed["preferences"].append(preference)
