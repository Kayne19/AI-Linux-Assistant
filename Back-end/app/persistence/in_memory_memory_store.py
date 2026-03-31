from persistence.memory_common import (
    MemorySnapshot,
    PROMOTED_FACT_KEYS,
    _clean_text,
    _display_fact_label,
    _relevance_score,
    _tokenize,
    _truncate,
    _utc_now,
)


def _iso(value):
    return value or None


class InMemoryMemoryStore:
    def __init__(self, project_id="in-memory", shared_state=None):
        self.project_id = str(project_id)
        self._shared_state = shared_state if shared_state is not None else self._new_state()

    def _new_state(self):
        return {
            "facts": {},
            "issues": {},
            "attempts": [],
            "constraints": {},
            "preferences": {},
            "candidates": [],
            "state": {},
            "next_issue_id": 1,
            "next_attempt_id": 1,
        }

    def set_project(self, project_id):
        self.project_id = str(project_id)

    @property
    def shared_state(self):
        return self._shared_state

    def _read_state(self, state_key):
        return self._shared_state["state"].get(state_key, "")

    def _write_state(self, state_key, state_value):
        self._shared_state["state"][state_key] = state_value or ""

    def get_system_facts(self):
        rows = sorted(
            self._shared_state["facts"].values(),
            key=lambda item: (item.get("updated_at") or "", item.get("fact_key") or ""),
            reverse=True,
        )
        return [
            {
                "fact_key": row["fact_key"],
                "fact_value": row["fact_value"],
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "confidence": row["confidence"],
                "verified": bool(row["verified"]),
                "observed_at": _iso(row["observed_at"]),
            }
            for row in rows
        ]

    def load_snapshot(self):
        profile_facts = self.get_system_facts()
        issues = sorted(
            self._shared_state["issues"].values(),
            key=lambda item: item.get("last_seen_at") or "",
            reverse=True,
        )[:12]
        attempts = sorted(
            self._shared_state["attempts"],
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )[:12]
        constraints = sorted(
            self._shared_state["constraints"].values(),
            key=lambda item: item.get("last_seen_at") or "",
            reverse=True,
        )
        preferences = sorted(
            self._shared_state["preferences"].values(),
            key=lambda item: item.get("last_seen_at") or "",
            reverse=True,
        )
        return {
            "profile": {fact["fact_key"]: fact["fact_value"] for fact in profile_facts},
            "issues": [
                {
                    "title": row["title"],
                    "category": row["category"],
                    "summary": row["summary"],
                    "status": row["status"],
                    "source_type": row["source_type"],
                    "last_seen_at": _iso(row["last_seen_at"]),
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
                    "created_at": _iso(row["created_at"]),
                }
                for row in attempts
            ],
            "constraints": [
                {
                    "constraint_key": row["constraint_key"],
                    "constraint_value": row["constraint_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in constraints
            ],
            "preferences": [
                {
                    "preference_key": row["preference_key"],
                    "preference_value": row["preference_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in preferences
            ],
            "session_summary": self._read_state("session_summary"),
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
            promoted_rank = PROMOTED_FACT_KEYS.index(key) if key in PROMOTED_FACT_KEYS else len(PROMOTED_FACT_KEYS)
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

    def get_relevant_memory(self, query, max_profile_facts=10, max_issues=3, max_attempts=5):
        query_tokens = _tokenize(query)
        profile_facts = self._select_profile_facts(
            self.get_system_facts(),
            query_tokens=query_tokens,
            max_items=max_profile_facts,
        )
        issues = list(self._shared_state["issues"].values())
        attempts = list(self._shared_state["attempts"])
        constraints = list(self._shared_state["constraints"].values())
        preferences = list(self._shared_state["preferences"].values())

        active_issues = []
        for row in issues:
            issue = {
                "id": row["id"],
                "title": row["title"],
                "category": row["category"],
                "summary": row["summary"],
                "status": row["status"],
                "source_type": row["source_type"],
                "last_seen_at": _iso(row["last_seen_at"]),
            }
            issue["relevance"] = _relevance_score(query_tokens, f"{row['title']} {row['summary']} {row['category']}")
            active_issues.append(issue)
        active_issues.sort(
            key=lambda item: (1 if item["status"] == "open" else 0, item["relevance"], item["last_seen_at"]),
            reverse=True,
        )
        active_issues = active_issues[:max_issues]

        issue_ids = {issue["id"] for issue in active_issues if issue.get("id")}
        title_by_issue_id = {row["id"]: row["title"] for row in issues}
        relevant_attempts = []
        for row in attempts:
            issue_title = title_by_issue_id.get(row.get("issue_id"), "")
            attempt = {
                "id": row["id"],
                "issue_id": row.get("issue_id"),
                "action": row["action"],
                "command": row["command"],
                "outcome": row["outcome"],
                "status": row["status"],
                "source_type": row["source_type"],
                "created_at": _iso(row["created_at"]),
                "issue_title": issue_title,
            }
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
            constraints=[
                {
                    "constraint_key": row["constraint_key"],
                    "constraint_value": row["constraint_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in constraints[:5]
            ],
            preferences=[
                {
                    "preference_key": row["preference_key"],
                    "preference_value": row["preference_value"],
                    "source_type": row["source_type"],
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in preferences[:5]
            ],
            session_summary=self._read_state("session_summary"),
        )

    def format_memory_snapshot(self, query, host_label=None):
        return self.get_relevant_memory(query).as_prompt_text()

    def list_candidates(self, max_results=25):
        rows = sorted(
            self._shared_state["candidates"],
            key=lambda item: item.get("updated_at") or "",
            reverse=True,
        )[:max_results]
        return [
            {
                "item_type": row["item_type"],
                "item_key": row["item_key"],
                "status": row["status"],
                "reason": row["reason"],
                "confidence": row["confidence"],
                "source_type": row["source_type"],
                "source_ref": row["source_ref"],
                "payload": row["payload"],
                "updated_at": _iso(row["updated_at"]),
            }
            for row in rows
        ]

    def format_debug_dump(self, query="system profile attempts issues preferences", max_candidates=20):
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
                    summary = f"{payload.get('fact_key', '')}={payload.get('fact_value', '')}"
                elif item_type == "issue":
                    summary = payload.get("title", "")
                elif item_type == "attempt":
                    summary = " | ".join(
                        part for part in [payload.get("action", ""), payload.get("command", ""), payload.get("outcome", "")]
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
        rows = sorted(
            self._shared_state["issues"].values(),
            key=lambda item: item.get("last_seen_at") or "",
            reverse=True,
        )
        scored = []
        for row in rows:
            text = f"{row['title']} {row['category']} {row['summary']}"
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, _iso(row["last_seen_at"]), row))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row in scored[: max(1, min(int(max_results), 8))]:
            lines.append(f"[{row['status']}] {row['title']} | {row['category']} | {row['summary']}")
        return "\n".join(lines)

    def search_attempts(self, query, max_results=5):
        query_tokens = _tokenize(query)
        issue_titles = {row["id"]: row["title"] for row in self._shared_state["issues"].values()}
        rows = sorted(
            self._shared_state["attempts"],
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        scored = []
        for row in rows:
            issue_title = issue_titles.get(row.get("issue_id"), "")
            text = " ".join([row["action"], row["command"], row["outcome"], row["status"], issue_title])
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, _iso(row["created_at"]), row, issue_title))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row, issue_title in scored[: max(1, min(int(max_results), 8))]:
            parts = [part for part in [row["action"], row["command"], row["outcome"], issue_title] if part]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def commit_resolution(self, resolution, user_question="", assistant_response=""):
        del user_question
        del assistant_response
        committed = getattr(resolution, "committed", None) or {}
        candidates = list(getattr(resolution, "candidates", []) or [])
        conflicts = list(getattr(resolution, "conflicts", []) or [])
        issue_ids = {}

        for fact in committed.get("facts", []):
            key = fact.get("fact_key", "")
            value = fact.get("fact_value", "")
            if not key or not value:
                continue
            now = _utc_now()
            row = self._shared_state["facts"].get(key)
            if row is None:
                self._shared_state["facts"][key] = {
                    "fact_key": key,
                    "fact_value": value,
                    "source_type": fact.get("source_type", "model"),
                    "source_ref": fact.get("source_ref", "conversation"),
                    "confidence": float(fact.get("confidence", 0.5) or 0.5),
                    "verified": bool(fact.get("verified", False)),
                    "observed_at": now,
                    "expires_at": fact.get("expires_at"),
                    "updated_at": now,
                }
            else:
                row.update(
                    {
                        "fact_value": value,
                        "source_type": fact.get("source_type", row["source_type"]),
                        "source_ref": fact.get("source_ref", row["source_ref"]),
                        "confidence": float(fact.get("confidence", row["confidence"]) or row["confidence"]),
                        "verified": bool(fact.get("verified", row["verified"])),
                        "observed_at": now,
                        "updated_at": now,
                    }
                )

        for issue in committed.get("issues", []):
            title = _clean_text(issue.get("title", ""))
            if not title:
                continue
            normalized_title = title.lower()
            now = _utc_now()
            row = self._shared_state["issues"].get(normalized_title)
            if row is None:
                row = {
                    "id": self._shared_state["next_issue_id"],
                    "title": title,
                    "normalized_title": normalized_title,
                    "category": issue.get("category", "general"),
                    "summary": _clean_text(issue.get("summary", "")),
                    "status": issue.get("status", "unknown"),
                    "source_type": issue.get("source_type", "model"),
                    "source_ref": issue.get("source_ref", "conversation"),
                    "confidence": float(issue.get("confidence", 0.5) or 0.5),
                    "created_at": now,
                    "last_seen_at": now,
                    "resolved_at": issue.get("resolved_at"),
                }
                self._shared_state["next_issue_id"] += 1
                self._shared_state["issues"][normalized_title] = row
            else:
                row.update(
                    {
                        "category": issue.get("category", row["category"]),
                        "summary": _clean_text(issue.get("summary", row["summary"])),
                        "status": issue.get("status", row["status"]),
                        "source_type": issue.get("source_type", row["source_type"]),
                        "source_ref": issue.get("source_ref", row["source_ref"]),
                        "confidence": float(issue.get("confidence", row["confidence"]) or row["confidence"]),
                        "last_seen_at": now,
                    }
                )
            issue_ids[normalized_title] = row["id"]

        default_issue_id = next(iter(issue_ids.values()), None)
        for attempt in committed.get("attempts", []):
            if not (attempt.get("action") or attempt.get("command")):
                continue
            issue_title = _clean_text(attempt.get("issue_title", "")).lower()
            linked_issue_id = issue_ids.get(issue_title, default_issue_id)
            self._shared_state["attempts"].append(
                {
                    "id": self._shared_state["next_attempt_id"],
                    "issue_id": linked_issue_id,
                    "action": _clean_text(attempt.get("action", "")),
                    "command": _clean_text(attempt.get("command", "")),
                    "outcome": _clean_text(attempt.get("outcome", "")),
                    "status": attempt.get("status", "unknown"),
                    "source_type": attempt.get("source_type", "model"),
                    "source_ref": attempt.get("source_ref", "conversation"),
                    "created_at": _utc_now(),
                }
            )
            self._shared_state["next_attempt_id"] += 1

        for constraint in committed.get("constraints", []):
            key = _clean_text(constraint.get("constraint_key", ""))
            value = _clean_text(constraint.get("constraint_value", ""))
            if not key or not value:
                continue
            now = _utc_now()
            item_key = (key, value)
            row = self._shared_state["constraints"].get(item_key)
            if row is None:
                self._shared_state["constraints"][item_key] = {
                    "constraint_key": key,
                    "constraint_value": value,
                    "source_type": constraint.get("source_type", "model"),
                    "source_ref": constraint.get("source_ref", "conversation"),
                    "created_at": now,
                    "last_seen_at": now,
                }
            else:
                row.update(
                    {
                        "source_type": constraint.get("source_type", row["source_type"]),
                        "source_ref": constraint.get("source_ref", row["source_ref"]),
                        "last_seen_at": now,
                    }
                )

        for preference in committed.get("preferences", []):
            key = _clean_text(preference.get("preference_key", ""))
            value = _clean_text(preference.get("preference_value", ""))
            if not key or not value:
                continue
            now = _utc_now()
            item_key = (key, value)
            row = self._shared_state["preferences"].get(item_key)
            if row is None:
                self._shared_state["preferences"][item_key] = {
                    "preference_key": key,
                    "preference_value": value,
                    "source_type": preference.get("source_type", "model"),
                    "source_ref": preference.get("source_ref", "conversation"),
                    "created_at": now,
                    "last_seen_at": now,
                }
            else:
                row.update(
                    {
                        "source_type": preference.get("source_type", row["source_type"]),
                        "source_ref": preference.get("source_ref", row["source_ref"]),
                        "last_seen_at": now,
                    }
                )

        self._shared_state["candidates"] = [
            {
                "item_type": item.get("item_type", "unknown"),
                "item_key": item.get("item_key", ""),
                "status": item.get("status", "candidate"),
                "reason": item.get("reason", ""),
                "confidence": float(item.get("confidence", 0.5) or 0.5),
                "source_type": item.get("source_type", "model"),
                "source_ref": item.get("source_ref", "conversation"),
                "payload": item.get("payload", {}),
                "updated_at": _utc_now(),
            }
            for item in candidates + conflicts
        ]
        self._write_state("session_summary", getattr(resolution, "session_summary", "") or "")
