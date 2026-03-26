import sqlite3
from pathlib import Path

from memory_resolver import MemoryResolution
from memory_store import MemoryStore


def test_memory_store_migrates_legacy_issue_and_attempt_tables(tmp_path):
    db_path = Path(tmp_path) / "legacy_memory.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE issues (
            title TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_title TEXT NOT NULL,
            attempted_solution TEXT NOT NULL,
            outcome TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO issues(title, status, summary, updated_at) VALUES(?, ?, ?, ?)",
        ("Docker permission denied", "open", "docker.sock permission problem", "2026-03-25T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO attempts(issue_title, attempted_solution, outcome, created_at) VALUES(?, ?, ?, ?)",
        ("Docker permission denied", "sudo apt install docker.io", "did not help", "2026-03-25T00:00:01+00:00"),
    )
    conn.commit()
    conn.close()

    store = MemoryStore(db_path=db_path)
    snapshot = store.load_snapshot()

    conn = sqlite3.connect(db_path)
    issue_columns = [row[1] for row in conn.execute("PRAGMA table_info(issues)").fetchall()]
    attempt_columns = [row[1] for row in conn.execute("PRAGMA table_info(attempts)").fetchall()]
    conn.close()

    assert "id" in issue_columns
    assert "host_id" in issue_columns
    assert "issue_id" in attempt_columns
    assert snapshot["issues"][0]["title"] == "Docker permission denied"
    assert snapshot["attempts"][0]["action"] == "sudo apt install docker.io"


def test_memory_store_commits_superseded_fact_and_preserves_audit_entry(tmp_path):
    db_path = Path(tmp_path) / "memory.db"
    store = MemoryStore(db_path=db_path)

    initial = MemoryResolution(
        committed={
            "facts": [
                {
                    "fact_key": "os.distribution",
                    "fact_value": "Debian 12",
                    "source_type": "user",
                    "source_ref": "user_question",
                    "confidence": 0.95,
                }
            ],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
        }
    )
    store.commit_resolution(initial, user_question="I am on Debian 12", assistant_response="")

    superseding = MemoryResolution(
        committed={
            "facts": [
                {
                    "fact_key": "os.distribution",
                    "fact_value": "Ubuntu 24.04",
                    "source_type": "user",
                    "source_ref": "user_question",
                    "confidence": 0.97,
                }
            ],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
        },
        conflicts=[
            {
                "item_type": "fact",
                "item_key": "os.distribution",
                "payload": {
                    "fact_key": "os.distribution",
                    "fact_value": "Debian 12",
                    "source_type": "memory",
                    "source_ref": "committed_memory",
                    "confidence": 1.0,
                    "replaced_by": "Ubuntu 24.04",
                },
                "status": "superseded",
                "reason": "superseded_by_user_update:Ubuntu 24.04",
                "confidence": 1.0,
                "source_type": "memory",
                "source_ref": "committed_memory",
            }
        ],
    )
    store.commit_resolution(
        superseding,
        user_question="Actually this machine is Ubuntu 24.04 now.",
        assistant_response="",
    )

    snapshot = store.load_snapshot()
    candidates = store.list_candidates()

    assert snapshot["profile"]["os.distribution"] == "Ubuntu 24.04"
    assert candidates[0]["status"] == "superseded"
    assert candidates[0]["payload"]["fact_value"] == "Debian 12"
    assert candidates[0]["payload"]["replaced_by"] == "Ubuntu 24.04"
