from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from durable_compute_plane.models import Base, ComputeJob
from durable_compute_plane.store import (
    ComputePlaneStore,
    ActiveJobLimitExceededError,
    ActiveScopeJobExistsError,
)
from durable_compute_plane.errors import JobOwnershipLostError


def _build_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return ComputePlaneStore(session_factory=session_factory), session_factory


def test_store_reuses_same_client_token_per_scope():
    store, _ = _build_store()
    first = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="same-token",
        payload={"task": "hello"},
    )
    second = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="same-token",
        payload={"task": "hello"},
    )
    assert second.id == first.id


def test_store_blocks_second_active_job_in_same_scope():
    store, _ = _build_store()
    store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
    )
    try:
        store.create_or_reuse_job(
            scope_key="scope-1",
            owner_key="owner-1",
            client_token="token-2",
        )
        assert False, "expected active scope conflict"
    except ActiveScopeJobExistsError:
        pass


def test_store_enforces_owner_cap_across_scopes():
    store, _ = _build_store()
    store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
        max_active_jobs_per_owner=1,
    )
    try:
        store.create_or_reuse_job(
            scope_key="scope-2",
            owner_key="owner-1",
            client_token="token-2",
            max_active_jobs_per_owner=1,
        )
        assert False, "expected owner cap enforcement"
    except ActiveJobLimitExceededError:
        pass


def test_store_completion_persists_done_event():
    store, _ = _build_store()
    job = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
    )
    store.claim_next_job("worker-1", lease_seconds=30)
    store.complete_job(
        job.id,
        worker_id="worker-1",
        result_json={"status": "ok"},
        done_payload={"meta": {"trace": ["START", "DONE"]}},
    )

    completed = store.get_job(job.id)
    events = store.list_events_after(job.id)

    assert completed.status == "completed"
    assert completed.result_json == {"status": "ok"}
    assert len(events) == 1
    assert events[0].type == "done"
    assert events[0].payload_json["result"] == {"status": "ok"}


def test_worker_owned_mutations_reject_stale_worker_after_reclaim():
    store, session_factory = _build_store()
    job = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
    )
    first_claim = store.claim_next_job("worker-1", lease_seconds=30)
    assert first_claim is not None

    with session_factory() as session:
        row = session.get(ComputeJob, job.id)
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

    reclaimed = store.claim_next_job("worker-2", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.worker_id == "worker-2"

    try:
        store.append_event_for_worker(job.id, "worker-1", "state", "START", None)
        assert False, "expected stale worker write rejection"
    except JobOwnershipLostError:
        pass


def test_pause_and_resume_keep_same_job_id():
    store, _ = _build_store()
    job = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
    )
    store.claim_next_job("worker-1", lease_seconds=30)
    store.request_pause(job.id)
    paused = store.mark_paused(
        job.id,
        worker_id="worker-1",
        checkpoint_state={"offset": 12},
    )
    resumed = store.resume_paused_job(
        job.id,
        checkpoint_state_patch={"offset": 18},
        resume_payload={"message": "continue"},
    )
    events = store.list_events_after(job.id)

    assert paused.status == "paused"
    assert resumed.id == job.id
    assert resumed.status == "queued"
    assert resumed.checkpoint_state_json == {"offset": 18}
    assert [event.code for event in events] == [
        "pause_requested",
        "paused",
        "checkpoint_state_updated",
        "resumed",
    ]
