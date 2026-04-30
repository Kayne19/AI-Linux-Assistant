from persistence.database import get_session_factory
from utils.time_utils import _iso, _utc_now
from persistence.memory_common import (
    MemorySnapshot,
    PROMOTED_FACT_KEYS,
    _clean_text,
    _display_fact_label,
    _relevance_score,
    _tokenize,
    _truncate,
)

try:
    from sqlalchemy import select
except ImportError:  # pragma: no cover - optional until SQLAlchemy is installed
    select = None

from persistence.postgres_models import (
    ProjectAttempt,
    ProjectConstraint,
    ProjectFact,
    ProjectIssue,
    ProjectMemoryCandidate,
    ProjectPreference,
    ProjectState,
)


class PostgresMemoryStore:
    def __init__(self, project_id, session_factory=None):
        if select is None:
            raise ImportError(
                "SQLAlchemy is required for PostgresMemoryStore. "
                "Install sqlalchemy and alembic in the AI-Linux-Assistant environment."
            )
        self.project_id = str(project_id)
        self.session_factory = session_factory or get_session_factory()
        self._snapshot_cache: dict | None = None
        self._fact_timestamp_snapshot: dict[str, str] | None = None

    def _session(self):
        return self.session_factory()

    def begin_turn(self):
        self._snapshot_cache = None
        self._fact_timestamp_snapshot = None

    def end_turn(self):
        self._snapshot_cache = None
        self._fact_timestamp_snapshot = None

    def _load_raw_data(self):
        if self._snapshot_cache is not None:
            return self._snapshot_cache
        with self._session() as session:
            fact_rows = list(
                session.scalars(
                    select(ProjectFact)
                    .where(ProjectFact.project_id == self.project_id)
                    .order_by(ProjectFact.updated_at.desc(), ProjectFact.fact_key.asc())
                )
            )
            issue_rows = list(
                session.scalars(
                    select(ProjectIssue)
                    .where(ProjectIssue.project_id == self.project_id)
                    .order_by(ProjectIssue.last_seen_at.desc())
                )
            )
            attempt_rows = list(
                session.scalars(
                    select(ProjectAttempt)
                    .where(ProjectAttempt.project_id == self.project_id)
                    .order_by(ProjectAttempt.created_at.desc())
                )
            )
            constraint_rows = list(
                session.scalars(
                    select(ProjectConstraint)
                    .where(ProjectConstraint.project_id == self.project_id)
                    .order_by(ProjectConstraint.last_seen_at.desc())
                )
            )
            preference_rows = list(
                session.scalars(
                    select(ProjectPreference)
                    .where(ProjectPreference.project_id == self.project_id)
                    .order_by(ProjectPreference.last_seen_at.desc())
                )
            )
            state_rows = list(
                session.scalars(
                    select(ProjectState).where(
                        ProjectState.project_id == self.project_id
                    )
                )
            )

            facts = [
                {
                    "fact_key": row.fact_key,
                    "fact_value": row.fact_value,
                    "source_type": row.source_type,
                    "source_ref": row.source_ref,
                    "confidence": row.confidence,
                    "verified": bool(row.verified),
                    "observed_at": _iso(row.observed_at),
                    "updated_at": _iso(row.updated_at),
                }
                for row in fact_rows
            ]
            issues = [
                {
                    "id": row.id,
                    "title": row.title,
                    "category": row.category,
                    "summary": row.summary,
                    "status": row.status,
                    "source_type": row.source_type,
                    "last_seen_at": _iso(row.last_seen_at),
                }
                for row in issue_rows
            ]
            attempts = [
                {
                    "id": row.id,
                    "issue_id": row.issue_id,
                    "action": row.action,
                    "command": row.command,
                    "outcome": row.outcome,
                    "status": row.status,
                    "source_type": row.source_type,
                    "created_at": _iso(row.created_at),
                }
                for row in attempt_rows
            ]
            constraints = [
                {
                    "constraint_key": row.constraint_key,
                    "constraint_value": row.constraint_value,
                    "source_type": row.source_type,
                    "last_seen_at": _iso(row.last_seen_at),
                }
                for row in constraint_rows
            ]
            preferences = [
                {
                    "preference_key": row.preference_key,
                    "preference_value": row.preference_value,
                    "source_type": row.source_type,
                    "last_seen_at": _iso(row.last_seen_at),
                }
                for row in preference_rows
            ]
            state_map = {row.state_key: row.state_value for row in state_rows}

        self._snapshot_cache = {
            "facts": facts,
            "issues": issues,
            "attempts": attempts,
            "constraints": constraints,
            "preferences": preferences,
            "state_map": state_map,
        }
        if self._fact_timestamp_snapshot is None:
            self._fact_timestamp_snapshot = {
                row.fact_key: _iso(row.updated_at or row.observed_at)
                for row in fact_rows
            }
        return self._snapshot_cache

    def is_snapshot_stale(self):
        """Return True if any fact has been modified since the current turn's snapshot.

        Compares fact (key, updated_at) tuples from a fresh DB read against the
        timestamp snapshot captured at the start of this turn.
        """
        if self._fact_timestamp_snapshot is None:
            return False
        with self._session() as session:
            fresh_rows = list(
                session.scalars(
                    select(ProjectFact).where(ProjectFact.project_id == self.project_id)
                )
            )
        fresh_map = {
            row.fact_key: _iso(row.updated_at or row.observed_at) for row in fresh_rows
        }
        snapshot_map = self._fact_timestamp_snapshot
        if set(fresh_map.keys()) != set(snapshot_map.keys()):
            return True
        for key, ts in fresh_map.items():
            if ts != snapshot_map.get(key):
                return True
        return False

    def load_snapshot_fresh(self):
        """Bypass the in-turn cache and load a current snapshot from the database.

        Also updates the timestamp snapshot so future staleness checks reflect this
        fresh state.
        """
        self._snapshot_cache = None
        self._fact_timestamp_snapshot = None
        return self.load_snapshot()

    def set_project(self, project_id):
        self.project_id = str(project_id)

    def _read_state(self, state_key):
        with self._session() as session:
            state = session.scalar(
                select(ProjectState).where(
                    ProjectState.project_id == self.project_id,
                    ProjectState.state_key == state_key,
                )
            )
            return state.state_value if state is not None else ""

    def _write_state(self, session, state_key, state_value):
        state = session.scalar(
            select(ProjectState).where(
                ProjectState.project_id == self.project_id,
                ProjectState.state_key == state_key,
            )
        )
        if state is None:
            state = ProjectState(
                project_id=self.project_id,
                state_key=state_key,
                state_value=state_value or "",
            )
            session.add(state)
            return
        state.state_value = state_value or ""
        state.updated_at = _utc_now()

    def get_system_facts(self):
        return self._load_raw_data()["facts"]

    def load_snapshot(self):
        raw = self._load_raw_data()
        issues = raw["issues"][:12]
        attempts = raw["attempts"][:12]
        return {
            "profile": {fact["fact_key"]: fact["fact_value"] for fact in raw["facts"]},
            "issues": [
                {
                    "title": row["title"],
                    "category": row["category"],
                    "summary": row["summary"],
                    "status": row["status"],
                    "source_type": row["source_type"],
                    "last_seen_at": row["last_seen_at"],
                }
                for row in issues
            ],
            "attempts": [
                {
                    "action": row["action"],
                    "command": row["command"],
                    "outcome": row["outcome"],
                    "status": row["status"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                }
                for row in attempts
            ],
            "constraints": [
                {
                    "constraint_key": row["constraint_key"],
                    "constraint_value": row["constraint_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": row["last_seen_at"],
                }
                for row in raw["constraints"]
            ],
            "preferences": [
                {
                    "preference_key": row["preference_key"],
                    "preference_value": row["preference_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": row["last_seen_at"],
                }
                for row in raw["preferences"]
            ],
            "session_summary": raw["state_map"].get("session_summary", ""),
            "fact_timestamps": self._fact_timestamp_snapshot or {},
        }

    def format_snapshot(self, snapshot):
        memory_snapshot = MemorySnapshot(
            host_label=self.project_id,
            profile_facts=[
                {"fact_key": key, "fact_value": value, "source_type": "memory"}
                for key, value in snapshot.get("profile", {}).items()
            ],
            active_issues=snapshot.get("issues", []),
            relevant_attempts=snapshot.get("attempts", []),
            constraints=snapshot.get("constraints", []),
            preferences=snapshot.get("preferences", []),
            session_summary=snapshot.get("session_summary", ""),
        )
        return memory_snapshot.as_prompt_text()

    def _select_profile_facts(self, facts, query_tokens, max_items):
        scored = []
        for fact in facts:
            key = fact["fact_key"]
            value = fact["fact_value"]
            promoted_rank = (
                PROMOTED_FACT_KEYS.index(key)
                if key in PROMOTED_FACT_KEYS
                else len(PROMOTED_FACT_KEYS)
            )
            relevance = _relevance_score(query_tokens, f"{key} {value}")
            scored.append((relevance, -promoted_rank, key, fact))
        scored.sort(reverse=True)
        selected = []
        seen_keys = set()
        for relevance, _, key, fact in scored:
            if key in seen_keys:
                continue
            if key in PROMOTED_FACT_KEYS or relevance > 0 or not query_tokens:
                selected.append(fact)
                seen_keys.add(key)
            if len(selected) >= max_items:
                break
        if not query_tokens and len(selected) < max_items:
            remaining = [fact for fact in facts if fact["fact_key"] not in seen_keys]
            selected.extend(remaining[: max_items - len(selected)])
        return selected[:max_items]

    def format_system_profile(self, host_label=None, max_facts=12):
        facts = self.get_system_facts()
        facts = self._select_profile_facts(facts, query_tokens=[], max_items=max_facts)
        if not facts:
            return ""
        lines = ["KNOWN SYSTEM PROFILE:"]
        for fact in facts:
            label = _display_fact_label(fact["fact_key"])
            lines.append(f"- {label}: {fact['fact_value']}")
        return "\n".join(lines)

    def get_relevant_memory(
        self, query, max_profile_facts=10, max_issues=3, max_attempts=5
    ):
        query_tokens = _tokenize(query)
        raw = self._load_raw_data()
        profile_facts = self._select_profile_facts(
            raw["facts"],
            query_tokens=query_tokens,
            max_items=max_profile_facts,
        )

        active_issues = []
        for row in raw["issues"]:
            issue = dict(row)
            issue["relevance"] = _relevance_score(
                query_tokens, f"{row['title']} {row['summary']} {row['category']}"
            )
            active_issues.append(issue)
        active_issues.sort(
            key=lambda item: (
                1 if item["status"] == "open" else 0,
                item["relevance"],
                item["last_seen_at"],
            ),
            reverse=True,
        )
        active_issues = active_issues[:max_issues]

        issue_ids = {issue["id"] for issue in active_issues if issue.get("id")}
        title_by_issue_id = {row["id"]: row["title"] for row in raw["issues"]}
        relevant_attempts = []
        for row in raw["attempts"]:
            issue_title = title_by_issue_id.get(row["issue_id"], "")
            attempt = dict(row)
            attempt["issue_title"] = issue_title
            attempt["relevance"] = _relevance_score(
                query_tokens,
                f"{row['action']} {row['command']} {row['outcome']} {issue_title}",
            )
            relevant_attempts.append(attempt)
        relevant_attempts.sort(
            key=lambda item: (
                1 if item.get("issue_id") in issue_ids else 0,
                item["relevance"],
                item["created_at"],
            ),
            reverse=True,
        )
        relevant_attempts = relevant_attempts[:max_attempts]

        return MemorySnapshot(
            host_label=self.project_id,
            profile_facts=profile_facts,
            active_issues=active_issues,
            relevant_attempts=relevant_attempts,
            constraints=raw["constraints"][:5],
            preferences=raw["preferences"][:5],
            session_summary=raw["state_map"].get("session_summary", ""),
        )

    def format_memory_snapshot(self, query, host_label=None):
        return self.get_relevant_memory(query).as_prompt_text()

    def list_candidates(self, max_results=25, chat_session_id=None):
        with self._session() as session:
            stmt = select(ProjectMemoryCandidate).where(
                ProjectMemoryCandidate.project_id == self.project_id
            )
            if chat_session_id is not None:
                stmt = stmt.where(
                    ProjectMemoryCandidate.chat_session_id == chat_session_id
                )
            stmt = stmt.order_by(
                ProjectMemoryCandidate.updated_at.desc(),
                ProjectMemoryCandidate.created_at.desc(),
            ).limit(max_results)
            rows = list(session.scalars(stmt))
        return [
            {
                "item_type": row.item_type,
                "item_key": row.item_key,
                "status": row.status,
                "reason": row.reason,
                "confidence": row.confidence,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "payload": row.value_json or {},
                "chat_session_id": row.chat_session_id,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ]

    def _replace_active_candidates(self, session, items, chat_session_id=""):
        session.query(ProjectMemoryCandidate).where(
            ProjectMemoryCandidate.project_id == self.project_id,
            ProjectMemoryCandidate.chat_session_id == (chat_session_id or ""),
            ProjectMemoryCandidate.status.in_(("candidate", "conflicted")),
        ).delete(synchronize_session=False)
        now = _utc_now()
        for item in items:
            session.add(
                ProjectMemoryCandidate(
                    project_id=self.project_id,
                    chat_session_id=chat_session_id or "",
                    item_type=item.get("item_type", "unknown"),
                    item_key=item.get("item_key", ""),
                    status=item.get("status", "candidate"),
                    reason=item.get("reason", ""),
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                    source_type=item.get("source_type", "model"),
                    source_ref=item.get("source_ref", "conversation"),
                    value_json=item.get("payload", {}),
                    created_at=now,
                    updated_at=now,
                )
            )

    def _upsert_superseded_history(self, session, items):
        for item in items:
            payload = item.get("payload", {}) or {}
            existing_rows = list(
                session.scalars(
                    select(ProjectMemoryCandidate).where(
                        ProjectMemoryCandidate.project_id == self.project_id,
                        ProjectMemoryCandidate.item_type
                        == item.get("item_type", "unknown"),
                        ProjectMemoryCandidate.item_key == item.get("item_key", ""),
                        ProjectMemoryCandidate.status == "superseded",
                    )
                )
            )
            matching_row = next(
                (
                    row
                    for row in existing_rows
                    if (row.reason or "") == item.get("reason", "")
                    and float(row.confidence or 0.5)
                    == float(item.get("confidence", 0.5) or 0.5)
                    and (row.source_type or "") == item.get("source_type", "model")
                    and (row.source_ref or "") == item.get("source_ref", "conversation")
                    and (row.value_json or {}) == payload
                ),
                None,
            )
            if matching_row is not None:
                matching_row.updated_at = _utc_now()
                continue
            now = _utc_now()
            session.add(
                ProjectMemoryCandidate(
                    project_id=self.project_id,
                    item_type=item.get("item_type", "unknown"),
                    item_key=item.get("item_key", ""),
                    status="superseded",
                    reason=item.get("reason", ""),
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                    source_type=item.get("source_type", "model"),
                    source_ref=item.get("source_ref", "conversation"),
                    value_json=payload,
                    created_at=now,
                    updated_at=now,
                )
            )

    def format_debug_dump(
        self, query="system profile attempts issues preferences", max_candidates=20
    ):
        sections = []

        profile = self.format_system_profile()
        if profile:
            sections.append(profile)

        relevant = self.get_relevant_memory(query)
        relevant_text = relevant.as_prompt_text()
        if relevant_text:
            sections.append("RELEVANT MEMORY VIEW:")
            sections.append(relevant_text)

        candidates = self.list_candidates(max_results=max_candidates)
        if candidates:
            sections.append("MEMORY CANDIDATES:")
            for item in candidates:
                payload = item.get("payload", {})
                summary = ""
                item_type = item.get("item_type")
                if item_type == "fact":
                    summary = (
                        f"{payload.get('fact_key', '')}={payload.get('fact_value', '')}"
                    )
                elif item_type == "issue":
                    summary = payload.get("title", "")
                elif item_type == "attempt":
                    summary = " | ".join(
                        part
                        for part in [
                            payload.get("action", ""),
                            payload.get("command", ""),
                            payload.get("outcome", ""),
                        ]
                        if part
                    )
                elif item_type == "constraint":
                    summary = f"{payload.get('constraint_key', '')}={payload.get('constraint_value', '')}"
                elif item_type == "preference":
                    summary = f"{payload.get('preference_key', '')}={payload.get('preference_value', '')}"
                summary = _truncate(summary or str(payload), limit=200)
                sections.append(
                    f"- [{item.get('status')}] {item_type} | {summary} | "
                    f"reason={item.get('reason')} | source={item.get('source_type')}"
                )
        return "\n".join(section for section in sections if section).strip()

    def search_issues(self, query, max_results=5):
        query_tokens = _tokenize(query)
        with self._session() as session:
            rows = list(
                session.scalars(
                    select(ProjectIssue)
                    .where(ProjectIssue.project_id == self.project_id)
                    .order_by(ProjectIssue.last_seen_at.desc())
                )
            )
        scored = []
        for row in rows:
            text = f"{row.title} {row.category} {row.summary}"
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, _iso(row.last_seen_at), row))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row in scored[: max(1, min(int(max_results), 8))]:
            lines.append(f"[{row.status}] {row.title} | {row.category} | {row.summary}")
        return "\n".join(lines)

    def search_attempts(self, query, max_results=5):
        query_tokens = _tokenize(query)
        with self._session() as session:
            rows = list(
                session.scalars(
                    select(ProjectAttempt)
                    .where(ProjectAttempt.project_id == self.project_id)
                    .order_by(ProjectAttempt.created_at.desc())
                )
            )
            issues = list(
                session.scalars(
                    select(ProjectIssue).where(
                        ProjectIssue.project_id == self.project_id
                    )
                )
            )
            issue_titles = {row.id: row.title for row in issues}
        scored = []
        for row in rows:
            issue_title = issue_titles.get(row.issue_id, "")
            text = " ".join(
                [row.action, row.command, row.outcome, row.status, issue_title]
            )
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, _iso(row.created_at), row, issue_title))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row, issue_title in scored[: max(1, min(int(max_results), 8))]:
            parts = [
                part
                for part in [row.action, row.command, row.outcome, issue_title]
                if part
            ]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def commit_resolution(
        self, resolution, user_question="", assistant_response="", chat_session_id=""
    ):
        committed = getattr(resolution, "committed", None) or {}
        candidates = list(getattr(resolution, "candidates", []) or [])
        conflicts = list(getattr(resolution, "conflicts", []) or [])
        issue_ids = {}

        with self._session() as session:
            for fact in committed.get("facts", []):
                key = fact.get("fact_key", "")
                value = fact.get("fact_value", "")
                if not key or not value:
                    continue
                row = session.scalar(
                    select(ProjectFact).where(
                        ProjectFact.project_id == self.project_id,
                        ProjectFact.fact_key == key,
                    )
                )
                if row is None:
                    row = ProjectFact(
                        project_id=self.project_id,
                        fact_key=key,
                        fact_value=value,
                        source_type=fact.get("source_type", "model"),
                        source_ref=fact.get("source_ref", "conversation"),
                        confidence=float(fact.get("confidence", 0.5) or 0.5),
                        verified=bool(fact.get("verified", False)),
                        observed_at=_utc_now(),
                    )
                    session.add(row)
                else:
                    row.fact_value = value
                    row.source_type = fact.get("source_type", row.source_type)
                    row.source_ref = fact.get("source_ref", row.source_ref)
                    row.confidence = float(
                        fact.get("confidence", row.confidence) or row.confidence
                    )
                    row.verified = bool(fact.get("verified", row.verified))
                    row.observed_at = _utc_now()
                    row.updated_at = _utc_now()

            for issue in committed.get("issues", []):
                title = _clean_text(issue.get("title", ""))
                if not title:
                    continue
                normalized_title = title.lower()
                row = session.scalar(
                    select(ProjectIssue).where(
                        ProjectIssue.project_id == self.project_id,
                        ProjectIssue.normalized_title == normalized_title,
                    )
                )
                if row is None:
                    row = ProjectIssue(
                        project_id=self.project_id,
                        title=title,
                        normalized_title=normalized_title,
                        category=issue.get("category", "general"),
                        summary=_clean_text(issue.get("summary", "")),
                        status=issue.get("status", "unknown"),
                        source_type=issue.get("source_type", "model"),
                        source_ref=issue.get("source_ref", "conversation"),
                        confidence=float(issue.get("confidence", 0.5) or 0.5),
                        created_at=_utc_now(),
                        last_seen_at=_utc_now(),
                    )
                    session.add(row)
                    session.flush()
                else:
                    row.category = issue.get("category", row.category)
                    row.summary = _clean_text(issue.get("summary", row.summary))
                    row.status = issue.get("status", row.status)
                    row.source_type = issue.get("source_type", row.source_type)
                    row.source_ref = issue.get("source_ref", row.source_ref)
                    row.confidence = float(
                        issue.get("confidence", row.confidence) or row.confidence
                    )
                    row.last_seen_at = _utc_now()
                issue_ids[normalized_title] = row.id

            default_issue_id = next(iter(issue_ids.values()), None)
            for attempt in committed.get("attempts", []):
                if not (attempt.get("action") or attempt.get("command")):
                    continue
                issue_title = _clean_text(attempt.get("issue_title", "")).lower()
                linked_issue_id = issue_ids.get(issue_title, default_issue_id)
                session.add(
                    ProjectAttempt(
                        project_id=self.project_id,
                        issue_id=linked_issue_id,
                        action=_clean_text(attempt.get("action", "")),
                        command=_clean_text(attempt.get("command", "")),
                        outcome=_clean_text(attempt.get("outcome", "")),
                        status=attempt.get("status", "unknown"),
                        source_type=attempt.get("source_type", "model"),
                        source_ref=attempt.get("source_ref", "conversation"),
                        created_at=_utc_now(),
                    )
                )

            for constraint in committed.get("constraints", []):
                key = _clean_text(constraint.get("constraint_key", ""))
                value = _clean_text(constraint.get("constraint_value", ""))
                if not key or not value:
                    continue
                row = session.scalar(
                    select(ProjectConstraint).where(
                        ProjectConstraint.project_id == self.project_id,
                        ProjectConstraint.constraint_key == key,
                        ProjectConstraint.constraint_value == value,
                    )
                )
                if row is None:
                    row = ProjectConstraint(
                        project_id=self.project_id,
                        constraint_key=key,
                        constraint_value=value,
                        source_type=constraint.get("source_type", "model"),
                        source_ref=constraint.get("source_ref", "conversation"),
                        created_at=_utc_now(),
                        last_seen_at=_utc_now(),
                    )
                    session.add(row)
                else:
                    row.source_type = constraint.get("source_type", row.source_type)
                    row.source_ref = constraint.get("source_ref", row.source_ref)
                    row.last_seen_at = _utc_now()

            for preference in committed.get("preferences", []):
                key = _clean_text(preference.get("preference_key", ""))
                value = _clean_text(preference.get("preference_value", ""))
                if not key or not value:
                    continue
                row = session.scalar(
                    select(ProjectPreference).where(
                        ProjectPreference.project_id == self.project_id,
                        ProjectPreference.preference_key == key,
                        ProjectPreference.preference_value == value,
                    )
                )
                if row is None:
                    row = ProjectPreference(
                        project_id=self.project_id,
                        preference_key=key,
                        preference_value=value,
                        source_type=preference.get("source_type", "model"),
                        source_ref=preference.get("source_ref", "conversation"),
                        created_at=_utc_now(),
                        last_seen_at=_utc_now(),
                    )
                    session.add(row)
                else:
                    row.source_type = preference.get("source_type", row.source_type)
                    row.source_ref = preference.get("source_ref", row.source_ref)
                    row.last_seen_at = _utc_now()

            active_candidate_items = [
                item
                for item in candidates + conflicts
                if item.get("status") != "superseded"
            ]
            superseded_items = [
                item for item in conflicts if item.get("status") == "superseded"
            ]
            self._replace_active_candidates(
                session, active_candidate_items, chat_session_id=chat_session_id
            )
            self._upsert_superseded_history(session, superseded_items)

            self._write_state(
                session,
                "session_summary",
                getattr(resolution, "session_summary", "") or "",
            )
            session.commit()
