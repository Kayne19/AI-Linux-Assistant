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
            committed={"facts": [], "issues": [], "attempts": [], "constraints": [], "preferences": []}
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
