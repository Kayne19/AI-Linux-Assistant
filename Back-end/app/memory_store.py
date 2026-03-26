import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
    return FACT_LABELS.get(fact_key, fact_key.replace(".", " ").replace("_", " ").title())


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _truncate(text, limit=220):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _clean_text(text):
    return " ".join((text or "").split())


def _tokenize(text):
    return [token for token in re.split(r"[^a-zA-Z0-9_.+-]+", (text or "").lower()) if len(token) >= 3]


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
                outcome = attempt.get("outcome", "").strip() or attempt.get("status", "").strip()
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


class MemoryStore:
    def __init__(self, db_path=None, host_label=DEFAULT_HOST_LABEL):
        self.db_path = Path(db_path or Path(__file__).resolve().with_name("assistant_memory.db"))
        self.host_label = host_label
        self._init_db()
        self.host_id = self._ensure_host(self.host_label, "Local System", is_local=True)
        self._migrate_legacy_tables()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    is_local INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS host_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    fact_key TEXT NOT NULL,
                    fact_value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    verified INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(host_id, fact_key),
                    FOREIGN KEY(host_id) REFERENCES hosts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    resolved_at TEXT,
                    UNIQUE(host_id, normalized_title),
                    FOREIGN KEY(host_id) REFERENCES hosts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    issue_id INTEGER,
                    action TEXT NOT NULL,
                    command TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(host_id) REFERENCES hosts(id),
                    FOREIGN KEY(issue_id) REFERENCES issues(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS constraints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    constraint_key TEXT NOT NULL,
                    constraint_value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(host_id, constraint_key, constraint_value),
                    FOREIGN KEY(host_id) REFERENCES hosts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(host_id, preference_key, preference_value),
                    FOREIGN KEY(host_id) REFERENCES hosts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    host_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(host_id) REFERENCES hosts(id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_preferences_host_key_value
                ON preferences(host_id, preference_key, preference_value)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _ensure_host(self, label, display_name, is_local=False):
        now = _utc_now()
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM hosts WHERE label = ?", (label,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE hosts SET display_name = ?, is_local = ?, updated_at = ? WHERE id = ?",
                    (display_name, 1 if is_local else 0, now, row[0]),
                )
                return row[0]
            cursor = conn.execute(
                "INSERT INTO hosts(label, display_name, is_local, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (label, display_name, 1 if is_local else 0, now, now),
            )
            return cursor.lastrowid

    def _table_columns(self, conn, table_name):
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]

    def _create_issues_table(self, conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                resolved_at TEXT,
                UNIQUE(host_id, normalized_title),
                FOREIGN KEY(host_id) REFERENCES hosts(id)
            )
            """
        )

    def _create_attempts_table(self, conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                host_id INTEGER NOT NULL,
                issue_id INTEGER,
                action TEXT NOT NULL,
                command TEXT NOT NULL,
                outcome TEXT NOT NULL,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(host_id) REFERENCES hosts(id),
                FOREIGN KEY(issue_id) REFERENCES issues(id)
            )
            """
        )

    def _migrate_preferences_table(self, conn):
        columns = set(self._table_columns(conn, "preferences"))
        if "host_id" not in columns:
            conn.execute("ALTER TABLE preferences ADD COLUMN host_id INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_preferences_host_key_value
            ON preferences(host_id, preference_key, preference_value)
            """
        )

    def _migrate_issues_table(self, conn):
        columns = set(self._table_columns(conn, "issues"))
        required = {
            "id",
            "host_id",
            "title",
            "normalized_title",
            "category",
            "summary",
            "status",
            "source_type",
            "source_ref",
            "confidence",
            "created_at",
            "last_seen_at",
            "resolved_at",
        }
        if required.issubset(columns):
            return

        legacy_rows = conn.execute("SELECT title, status, summary, updated_at FROM issues").fetchall()
        conn.execute("ALTER TABLE issues RENAME TO issues_legacy")
        self._create_issues_table(conn)
        now = _utc_now()
        for title, status, summary, updated_at in legacy_rows:
            normalized_title = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
            stamp = updated_at or now
            conn.execute(
                """
                INSERT INTO issues(
                    host_id, title, normalized_title, category, summary, status,
                    source_type, source_ref, confidence, created_at, last_seen_at, resolved_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.host_id,
                    _truncate(title, limit=120),
                    normalized_title,
                    "general",
                    _truncate(summary, limit=260),
                    (status or "unknown").lower(),
                    "model",
                    "legacy_migration",
                    0.5,
                    stamp,
                    stamp,
                    stamp if (status or "").lower() == "resolved" else None,
                ),
            )
        conn.execute("DROP TABLE issues_legacy")

    def _migrate_attempts_table(self, conn):
        columns = set(self._table_columns(conn, "attempts"))
        required = {
            "id",
            "host_id",
            "issue_id",
            "action",
            "command",
            "outcome",
            "status",
            "source_type",
            "source_ref",
            "created_at",
        }
        if required.issubset(columns):
            return

        legacy_rows = conn.execute(
            "SELECT issue_title, attempted_solution, outcome, created_at FROM attempts"
        ).fetchall()
        issue_map = {
            row[0].lower(): row[1]
            for row in conn.execute(
                "SELECT title, id FROM issues WHERE host_id = ?",
                (self.host_id,),
            ).fetchall()
        }
        conn.execute("ALTER TABLE attempts RENAME TO attempts_legacy")
        self._create_attempts_table(conn)
        now = _utc_now()
        for issue_title, attempted_solution, outcome, created_at in legacy_rows:
            linked_issue_id = issue_map.get((issue_title or "").lower())
            conn.execute(
                """
                INSERT INTO attempts(
                    host_id, issue_id, action, command, outcome, status,
                    source_type, source_ref, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.host_id,
                    linked_issue_id,
                    _truncate(attempted_solution, limit=180),
                    "",
                    _truncate(outcome, limit=180),
                    "unknown",
                    "model",
                    "legacy_migration",
                    created_at or now,
                ),
            )
        conn.execute("DROP TABLE attempts_legacy")

    def _migrate_legacy_tables(self):
        with self._connect() as conn:
            self._migrate_preferences_table(conn)
            self._migrate_issues_table(conn)
            self._migrate_attempts_table(conn)

    def _read_state(self, key, default=""):
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def _write_state(self, key, value):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def _upsert_fact_conn(
        self,
        conn,
        host_id,
        fact_key,
        fact_value,
        source_type,
        source_ref,
        confidence=1.0,
        verified=False,
    ):
        fact_value = _clean_text(fact_value)
        if not fact_value:
            return
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO host_facts(
                host_id, fact_key, fact_value, source_type, source_ref,
                confidence, verified, observed_at, expires_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(host_id, fact_key) DO UPDATE SET
                fact_value = excluded.fact_value,
                source_type = excluded.source_type,
                source_ref = excluded.source_ref,
                confidence = excluded.confidence,
                verified = excluded.verified,
                observed_at = excluded.observed_at,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (
                host_id,
                fact_key,
                fact_value,
                source_type,
                source_ref,
                float(confidence),
                1 if verified else 0,
                now,
                None,
                now,
            ),
        )

    def _upsert_issue_conn(
        self,
        conn,
        host_id,
        title,
        category,
        summary,
        status,
        source_type,
        source_ref,
        confidence=0.8,
    ):
        title = _truncate(title, limit=120)
        normalized_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if not normalized_title:
            return None
        summary = _truncate(summary, limit=260)
        now = _utc_now()
        row = conn.execute(
            "SELECT id FROM issues WHERE host_id = ? AND normalized_title = ?",
            (host_id, normalized_title),
        ).fetchone()
        if row:
            issue_id = row[0]
            resolved_at = now if status == "resolved" else None
            conn.execute(
                """
                UPDATE issues
                SET title = ?, category = ?, summary = ?, status = ?, source_type = ?,
                    source_ref = ?, confidence = ?, last_seen_at = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    category,
                    summary,
                    status,
                    source_type,
                    source_ref,
                    float(confidence),
                    now,
                    resolved_at,
                    issue_id,
                ),
            )
            return issue_id

        cursor = conn.execute(
            """
            INSERT INTO issues(
                host_id, title, normalized_title, category, summary, status,
                source_type, source_ref, confidence, created_at, last_seen_at, resolved_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                host_id,
                title,
                normalized_title,
                category,
                summary,
                status,
                source_type,
                source_ref,
                float(confidence),
                now,
                now,
                now if status == "resolved" else None,
            ),
        )
        return cursor.lastrowid

    def _insert_attempt_conn(
        self,
        conn,
        host_id,
        issue_id,
        action,
        command,
        outcome,
        status,
        source_type,
        source_ref,
    ):
        action = _truncate(action, limit=180)
        command = _truncate(command, limit=180)
        outcome = _truncate(outcome, limit=180)
        if not action and not command:
            return
        now = _utc_now()
        recent_duplicate = conn.execute(
            """
            SELECT id FROM attempts
            WHERE host_id = ? AND action = ? AND command = ? AND outcome = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (host_id, action, command, outcome),
        ).fetchone()
        if recent_duplicate:
            return
        conn.execute(
            """
            INSERT INTO attempts(
                host_id, issue_id, action, command, outcome, status,
                source_type, source_ref, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                host_id,
                issue_id,
                action,
                command,
                outcome,
                status,
                source_type,
                source_ref,
                now,
            ),
        )

    def _upsert_constraint_conn(self, conn, host_id, constraint_key, constraint_value, source_type, source_ref):
        constraint_value = _truncate(constraint_value, limit=160)
        if not constraint_key or not constraint_value:
            return
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO constraints(
                host_id, constraint_key, constraint_value, source_type, source_ref, created_at, last_seen_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(host_id, constraint_key, constraint_value) DO UPDATE SET
                source_type = excluded.source_type,
                source_ref = excluded.source_ref,
                last_seen_at = excluded.last_seen_at
            """,
            (host_id, constraint_key, constraint_value, source_type, source_ref, now, now),
        )

    def _upsert_preference_conn(self, conn, host_id, preference_key, preference_value, source_type, source_ref):
        preference_value = _truncate(preference_value, limit=160)
        if not preference_key or not preference_value:
            return
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO preferences(
                host_id, preference_key, preference_value, source_type, source_ref, created_at, last_seen_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(host_id, preference_key, preference_value) DO UPDATE SET
                source_type = excluded.source_type,
                source_ref = excluded.source_ref,
                last_seen_at = excluded.last_seen_at
            """,
            (host_id, preference_key, preference_value, source_type, source_ref, now, now),
        )

    def _insert_candidate_conn(self, conn, item_type, item_key, value_json, status, reason, confidence, source_type, source_ref):
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO memory_candidates(
                host_id, item_type, item_key, value_json, status, reason,
                confidence, source_type, source_ref, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.host_id,
                item_type,
                item_key,
                value_json,
                status,
                reason,
                float(confidence),
                source_type,
                source_ref,
                now,
                now,
            ),
        )

    def get_system_facts(self, host_label=None):
        host_label = host_label or self.host_label
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.fact_key, f.fact_value, f.source_type, f.source_ref, f.confidence, f.verified, f.observed_at
                FROM host_facts f
                JOIN hosts h ON h.id = f.host_id
                WHERE h.label = ?
                ORDER BY f.updated_at DESC, f.fact_key ASC
                """,
                (host_label,),
            ).fetchall()
        return [
            {
                "fact_key": row[0],
                "fact_value": row[1],
                "source_type": row[2],
                "source_ref": row[3],
                "confidence": row[4],
                "verified": bool(row[5]),
                "observed_at": row[6],
            }
            for row in rows
        ]

    def load_snapshot(self):
        profile_facts = self.get_system_facts()
        with self._connect() as conn:
            issues = conn.execute(
                """
                SELECT title, category, summary, status, source_type, last_seen_at
                FROM issues
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                LIMIT 12
                """,
                (self.host_id,),
            ).fetchall()
            attempts = conn.execute(
                """
                SELECT action, command, outcome, status, source_type, created_at
                FROM attempts
                WHERE host_id = ?
                ORDER BY created_at DESC
                LIMIT 12
                """,
                (self.host_id,),
            ).fetchall()
            constraints = conn.execute(
                """
                SELECT constraint_key, constraint_value, source_type, last_seen_at
                FROM constraints
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()
            preferences = conn.execute(
                """
                SELECT preference_key, preference_value, source_type, last_seen_at
                FROM preferences
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()
        return {
            "profile": {fact["fact_key"]: fact["fact_value"] for fact in profile_facts},
            "issues": [
                {
                    "title": row[0],
                    "category": row[1],
                    "summary": row[2],
                    "status": row[3],
                    "source_type": row[4],
                    "last_seen_at": row[5],
                }
                for row in issues
            ],
            "attempts": [
                {
                    "action": row[0],
                    "command": row[1],
                    "outcome": row[2],
                    "status": row[3],
                    "source_type": row[4],
                    "created_at": row[5],
                }
                for row in attempts
            ],
            "constraints": [
                {
                    "constraint_key": row[0],
                    "constraint_value": row[1],
                    "source_type": row[2],
                    "last_seen_at": row[3],
                }
                for row in constraints
            ],
            "preferences": [
                {
                    "preference_key": row[0],
                    "preference_value": row[1],
                    "source_type": row[2],
                    "last_seen_at": row[3],
                }
                for row in preferences
            ],
            "session_summary": self._read_state("session_summary"),
        }

    def format_snapshot(self, snapshot):
        memory_snapshot = MemorySnapshot(
            host_label=self.host_label,
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

    def format_system_profile(self, host_label=None, max_facts=12):
        facts = self.get_system_facts(host_label=host_label)
        facts = self._select_profile_facts(facts, query_tokens=[], max_items=max_facts)
        if not facts:
            return ""
        lines = ["KNOWN SYSTEM PROFILE:"]
        for fact in facts:
            label = _display_fact_label(fact["fact_key"])
            lines.append(f"- {label}: {fact['fact_value']}")
        return "\n".join(lines)

    def get_relevant_memory(self, query, host_label=None, max_profile_facts=10, max_issues=3, max_attempts=5):
        host_label = host_label or self.host_label
        query_tokens = _tokenize(query)
        profile_facts = self._select_profile_facts(
            self.get_system_facts(host_label=host_label),
            query_tokens=query_tokens,
            max_items=max_profile_facts,
        )

        with self._connect() as conn:
            issues = conn.execute(
                """
                SELECT id, title, category, summary, status, source_type, last_seen_at
                FROM issues
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()
            attempts = conn.execute(
                """
                SELECT a.id, a.issue_id, a.action, a.command, a.outcome, a.status, a.source_type, a.created_at,
                       COALESCE(i.title, '')
                FROM attempts a
                LEFT JOIN issues i ON i.id = a.issue_id
                WHERE a.host_id = ?
                ORDER BY a.created_at DESC
                """,
                (self.host_id,),
            ).fetchall()
            constraints = conn.execute(
                """
                SELECT constraint_key, constraint_value, source_type, last_seen_at
                FROM constraints
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()
            preferences = conn.execute(
                """
                SELECT preference_key, preference_value, source_type, last_seen_at
                FROM preferences
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()

        active_issues = []
        for row in issues:
            issue = {
                "id": row[0],
                "title": row[1],
                "category": row[2],
                "summary": row[3],
                "status": row[4],
                "source_type": row[5],
                "last_seen_at": row[6],
            }
            issue["relevance"] = _relevance_score(query_tokens, f"{issue['title']} {issue['summary']} {issue['category']}")
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
        relevant_attempts = []
        for row in attempts:
            attempt = {
                "id": row[0],
                "issue_id": row[1],
                "action": row[2],
                "command": row[3],
                "outcome": row[4],
                "status": row[5],
                "source_type": row[6],
                "created_at": row[7],
                "issue_title": row[8],
            }
            attempt["relevance"] = _relevance_score(
                query_tokens,
                f"{attempt['action']} {attempt['command']} {attempt['outcome']} {attempt['issue_title']}",
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
            host_label=host_label,
            profile_facts=profile_facts,
            active_issues=active_issues,
            relevant_attempts=relevant_attempts,
            constraints=[
                {
                    "constraint_key": row[0],
                    "constraint_value": row[1],
                    "source_type": row[2],
                    "last_seen_at": row[3],
                }
                for row in constraints[:5]
            ],
            preferences=[
                {
                    "preference_key": row[0],
                    "preference_value": row[1],
                    "source_type": row[2],
                    "last_seen_at": row[3],
                }
                for row in preferences[:5]
            ],
            session_summary=self._read_state("session_summary"),
        )

    def format_memory_snapshot(self, query, host_label=None):
        return self.get_relevant_memory(query, host_label=host_label).as_prompt_text()

    def list_candidates(self, max_results=25):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT item_type, item_key, status, reason, confidence, source_type, source_ref, value_json, updated_at
                FROM memory_candidates
                WHERE host_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (self.host_id, max_results),
            ).fetchall()
        results = []
        for row in rows:
            try:
                payload = json.loads(row[7])
            except json.JSONDecodeError:
                payload = {"raw": row[7]}
            results.append(
                {
                    "item_type": row[0],
                    "item_key": row[1],
                    "status": row[2],
                    "reason": row[3],
                    "confidence": row[4],
                    "source_type": row[5],
                    "source_ref": row[6],
                    "payload": payload,
                    "updated_at": row[8],
                }
            )
        return results

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
                summary = _truncate(summary or json.dumps(payload, sort_keys=True), limit=200)
                sections.append(
                    f"- [{item.get('status')}] {item_type} | {summary} | "
                    f"reason={item.get('reason')} | source={item.get('source_type')}"
                )

        return "\n".join(section for section in sections if section).strip()

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

    def search_issues(self, query, max_results=5):
        query_tokens = _tokenize(query)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT title, category, summary, status, last_seen_at
                FROM issues
                WHERE host_id = ?
                ORDER BY last_seen_at DESC
                """,
                (self.host_id,),
            ).fetchall()
        scored = []
        for row in rows:
            text = f"{row[0]} {row[1]} {row[2]}"
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, row[4], row))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row in scored[: max(1, min(int(max_results), 8))]:
            lines.append(f"[{row[3]}] {row[0]} | {row[1]} | {row[2]}")
        return "\n".join(lines)

    def search_attempts(self, query, max_results=5):
        query_tokens = _tokenize(query)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.action, a.command, a.outcome, a.status, a.created_at, COALESCE(i.title, '')
                FROM attempts a
                LEFT JOIN issues i ON i.id = a.issue_id
                WHERE a.host_id = ?
                ORDER BY a.created_at DESC
                """,
                (self.host_id,),
            ).fetchall()
        scored = []
        for row in rows:
            text = " ".join(row[:4]) + " " + row[5]
            score = _relevance_score(query_tokens, text)
            if query_tokens and score == 0:
                continue
            scored.append((score, row[4], row))
        scored.sort(reverse=True)
        if not scored:
            return ""
        lines = []
        for _, _, row in scored[: max(1, min(int(max_results), 8))]:
            parts = [part for part in [row[0], row[1], row[2], row[5]] if part]
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def commit_resolution(self, resolution, user_question="", assistant_response=""):
        committed = getattr(resolution, "committed", None) or {}
        candidates = list(getattr(resolution, "candidates", []) or [])
        conflicts = list(getattr(resolution, "conflicts", []) or [])
        issue_ids = {}
        with self._connect() as conn:
            for fact in committed.get("facts", []):
                self._upsert_fact_conn(
                    conn,
                    self.host_id,
                    fact.get("fact_key", ""),
                    fact.get("fact_value", ""),
                    fact.get("source_type", "model"),
                    fact.get("source_ref", "conversation"),
                    confidence=fact.get("confidence", 0.5),
                    verified=fact.get("verified", False),
                )

            for issue in committed.get("issues", []):
                issue_id = self._upsert_issue_conn(
                    conn,
                    self.host_id,
                    issue.get("title", ""),
                    issue.get("category", "general"),
                    issue.get("summary", ""),
                    issue.get("status", "unknown"),
                    issue.get("source_type", "model"),
                    issue.get("source_ref", "conversation"),
                    confidence=issue.get("confidence", 0.5),
                )
                if issue_id is not None:
                    issue_ids[issue.get("title", "").lower()] = issue_id

            default_issue_id = next(iter(issue_ids.values()), None)
            for attempt in committed.get("attempts", []):
                linked_issue_id = default_issue_id
                issue_title = (attempt.get("issue_title") or "").lower()
                if issue_title:
                    linked_issue_id = issue_ids.get(issue_title, linked_issue_id)
                self._insert_attempt_conn(
                    conn,
                    self.host_id,
                    linked_issue_id,
                    attempt.get("action", ""),
                    attempt.get("command", ""),
                    attempt.get("outcome", ""),
                    attempt.get("status", "unknown"),
                    attempt.get("source_type", "model"),
                    attempt.get("source_ref", "conversation"),
                )

            for constraint in committed.get("constraints", []):
                self._upsert_constraint_conn(
                    conn,
                    self.host_id,
                    constraint.get("constraint_key", ""),
                    constraint.get("constraint_value", ""),
                    constraint.get("source_type", "model"),
                    constraint.get("source_ref", "conversation"),
                )

            for preference in committed.get("preferences", []):
                self._upsert_preference_conn(
                    conn,
                    self.host_id,
                    preference.get("preference_key", ""),
                    preference.get("preference_value", ""),
                    preference.get("source_type", "model"),
                    preference.get("source_ref", "conversation"),
                )

            for item in candidates + conflicts:
                self._insert_candidate_conn(
                    conn,
                    item.get("item_type", "unknown"),
                    item.get("item_key", ""),
                    json.dumps(item.get("payload", {}), sort_keys=True),
                    item.get("status", "candidate"),
                    item.get("reason", "unresolved"),
                    item.get("confidence", 0.5),
                    item.get("source_type", "model"),
                    item.get("source_ref", "conversation"),
                )

        session_summary = getattr(resolution, "session_summary", "") or self._update_session_summary(
            user_question,
            assistant_response,
        )
        self._write_state("session_summary", session_summary)

    def _update_session_summary(self, user_question, assistant_response):
        existing = self._read_state("session_summary", "")
        entries = [line for line in existing.splitlines() if line.strip()]
        entries.append(f"User: {_truncate(user_question, limit=160)}")
        entries.append(f"Assistant: {_truncate(assistant_response, limit=160)}")
        return "\n".join(entries[-12:])
