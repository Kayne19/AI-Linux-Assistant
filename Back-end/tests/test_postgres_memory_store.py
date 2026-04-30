from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.memory_resolver import MemoryResolution
from persistence.database import Base
from persistence.postgres_memory_store import PostgresMemoryStore
from persistence.postgres_models import Project, User


def _build_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with session_factory() as session:
        user = User(username="memory-user")
        session.add(user)
        session.flush()
        project = Project(user_id=user.id, name="Memory Project", description="")
        session.add(project)
        session.commit()
        session.refresh(project)
    return PostgresMemoryStore(project.id, session_factory=session_factory)


def test_postgres_memory_store_preserves_superseded_fact_history_across_commits():
    store = _build_store()

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
    store.commit_resolution(initial)

    first_supersede = MemoryResolution(
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
    store.commit_resolution(first_supersede)

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

    second_supersede = MemoryResolution(
        committed={
            "facts": [
                {
                    "fact_key": "os.distribution",
                    "fact_value": "Fedora 42",
                    "source_type": "user",
                    "source_ref": "user_question",
                    "confidence": 0.98,
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
                    "fact_value": "Ubuntu 24.04",
                    "source_type": "memory",
                    "source_ref": "committed_memory",
                    "confidence": 1.0,
                    "replaced_by": "Fedora 42",
                },
                "status": "superseded",
                "reason": "superseded_by_user_update:Fedora 42",
                "confidence": 1.0,
                "source_type": "memory",
                "source_ref": "committed_memory",
            }
        ],
    )
    store.commit_resolution(second_supersede)

    snapshot = store.load_snapshot()
    candidates = store.list_candidates()

    assert snapshot["profile"]["os.distribution"] == "Fedora 42"
    superseded_values = [
        item["payload"]["fact_value"]
        for item in candidates
        if item["status"] == "superseded" and item["item_key"] == "os.distribution"
    ]
    assert set(superseded_values) == {"Debian 12", "Ubuntu 24.04"}


def test_postgres_memory_store_concurrent_chats_do_not_clobber_candidates():
    """Two concurrent turns in different chats of the same project must both land
    their candidates without one chat's _replace_active_candidates wiping out
    the other chat's uncommitted candidates."""
    from agents.memory_resolver import MemoryResolution

    store = _build_store()

    # Chat A commits a candidate (no commit to main tables, just a candidate)
    chat_a_resolution = MemoryResolution(
        committed={
            "facts": [],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
        },
        candidates=[
            {
                "item_type": "fact",
                "item_key": "os.distribution",
                "status": "candidate",
                "reason": "new_fact",
                "confidence": 0.85,
                "source_type": "model",
                "source_ref": "conversation",
                "payload": {"fact_key": "os.distribution", "fact_value": "Arch Linux"},
            },
            {
                "item_type": "fact",
                "item_key": "editor",
                "status": "candidate",
                "reason": "new_fact",
                "confidence": 0.9,
                "source_type": "user",
                "source_ref": "user_question",
                "payload": {"fact_key": "editor", "fact_value": "neovim"},
            },
        ],
    )
    store.commit_resolution(chat_a_resolution, chat_session_id="chat-a")

    # Chat B commits different candidates on the same project
    chat_b_resolution = MemoryResolution(
        committed={
            "facts": [],
            "issues": [],
            "attempts": [],
            "constraints": [],
            "preferences": [],
        },
        candidates=[
            {
                "item_type": "fact",
                "item_key": "os.distribution",
                "status": "candidate",
                "reason": "new_fact",
                "confidence": 0.8,
                "source_type": "model",
                "source_ref": "conversation",
                "payload": {"fact_key": "os.distribution", "fact_value": "Fedora 42"},
            },
        ],
    )
    store.commit_resolution(chat_b_resolution, chat_session_id="chat-b")

    # Both chats' candidates should coexist
    # Verify Chat A still has both its candidates
    chat_a_candidates = [
        c
        for c in store.list_candidates(chat_session_id="chat-a")
        if c["status"] == "candidate"
    ]
    assert len(chat_a_candidates) == 2, (
        f"Expected 2 candidates for chat-a, got {len(chat_a_candidates)}"
    )

    # Verify Chat B still has its candidate
    chat_b_candidates = [
        c
        for c in store.list_candidates(chat_session_id="chat-b")
        if c["status"] == "candidate"
    ]
    assert len(chat_b_candidates) == 1, (
        f"Expected 1 candidate for chat-b, got {len(chat_b_candidates)}"
    )

    # Verify the values differ per chat (same fact_key, different values)
    chat_a_os = next(
        c["payload"].get("fact_value")
        for c in chat_a_candidates
        if c["item_key"] == "os.distribution"
    )
    chat_b_os = next(
        c["payload"].get("fact_value")
        for c in chat_b_candidates
        if c["item_key"] == "os.distribution"
    )
    assert chat_a_os == "Arch Linux", f"Chat A should have Arch Linux, got {chat_a_os}"
    assert chat_b_os == "Fedora 42", f"Chat B should have Fedora 42, got {chat_b_os}"
