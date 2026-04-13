from agents.memory_resolver import MemoryResolution
from persistence.in_memory_memory_store import InMemoryMemoryStore


def test_in_memory_store_commits_superseded_fact_and_preserves_candidate_audit():
    store = InMemoryMemoryStore(project_id="test-project")

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

    store.commit_resolution(
        MemoryResolution(
            committed={
                "facts": [],
                "issues": [],
                "attempts": [],
                "constraints": [],
                "preferences": [],
            }
        )
    )

    snapshot = store.load_snapshot()
    candidates = store.list_candidates()

    assert snapshot["profile"]["os.distribution"] == "Ubuntu 24.04"
    superseded = [item for item in candidates if item["status"] == "superseded"]
    assert len(superseded) == 1
    assert superseded[0]["payload"]["fact_value"] == "Debian 12"
    assert superseded[0]["payload"]["replaced_by"] == "Ubuntu 24.04"


def test_in_memory_store_persists_issue_attempt_and_session_summary():
    store = InMemoryMemoryStore(project_id="test-project")

    resolution = MemoryResolution(
        committed={
            "facts": [],
            "issues": [
                {
                    "title": "Docker permission denied",
                    "category": "docker",
                    "summary": "docker.sock permission problem",
                    "status": "open",
                }
            ],
            "attempts": [
                {
                    "issue_title": "Docker permission denied",
                    "action": "Install Docker",
                    "command": "sudo apt install docker.io",
                    "outcome": "did not help",
                    "status": "failed",
                }
            ],
            "constraints": [],
            "preferences": [],
        },
        session_summary="User still blocked on Docker permissions.",
    )

    store.commit_resolution(resolution)
    snapshot = store.load_snapshot()

    assert snapshot["issues"][0]["title"] == "Docker permission denied"
    assert snapshot["attempts"][0]["action"] == "Install Docker"
    assert snapshot["session_summary"] == "User still blocked on Docker permissions."
