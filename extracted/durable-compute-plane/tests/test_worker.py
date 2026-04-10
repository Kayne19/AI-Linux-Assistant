from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from durable_compute_plane.errors import JobPausedError
from durable_compute_plane.models import Base
from durable_compute_plane.store import ComputePlaneStore
from durable_compute_plane.worker import JobExecutionResult, LeasedJobWorkerService, WorkerSettings


def _build_store():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return ComputePlaneStore(session_factory=session_factory)


class _ResumableWorker(LeasedJobWorkerService):
    def execute_job(self, job, context):
        context.emit_state("START")
        if not job.checkpoint_state_json:
            context.push_output_delta("hello ")
            context.begin_scoped_output({"stream": "logs"})
            context.push_scoped_output_delta({"stream": "logs"}, "phase-1")
            context.flush_scoped_output({"stream": "logs"})
            context.finish_scoped_output({"stream": "logs"})
            raise JobPausedError(
                checkpoint_state={"resume_from": "phase-2"},
                payload={"message": "pausing"},
            )
        context.emit_event("resumed_step", {"resume_from": job.checkpoint_state_json.get("resume_from")})
        context.push_output_delta("world")
        return JobExecutionResult(
            result_json={"output_text": "hello world"},
            done_payload={"result": {"output_text": "hello world"}},
        )


def test_worker_can_pause_resume_and_complete_same_job():
    store = _build_store()
    job = store.create_or_reuse_job(
        scope_key="scope-1",
        owner_key="owner-1",
        client_token="token-1",
    )
    claimed = store.claim_next_job("worker-1", lease_seconds=30)
    service = _ResumableWorker(store=store, settings=WorkerSettings())
    service._handle_claimed_job(claimed, "worker-1")

    paused = store.get_job(job.id)
    assert paused.status == "paused"
    assert paused.checkpoint_state_json == {"resume_from": "phase-2"}

    resumed = store.resume_paused_job(job.id)
    reclaimed = store.claim_next_job("worker-2", lease_seconds=30)
    assert resumed.id == job.id
    assert reclaimed.id == job.id

    service._handle_claimed_job(reclaimed, "worker-2")

    finished = store.get_job(job.id)
    events = store.list_events_after(job.id)

    assert finished.status == "completed"
    assert finished.partial_output_text == "hello world"
    assert any(event.code == "scoped_text_checkpoint" for event in events)
    assert events[-1].type == "done"
