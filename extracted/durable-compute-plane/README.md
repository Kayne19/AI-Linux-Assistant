# Durable Compute Plane

This is a standalone base package extracted from the AI Linux Assistant durable run queue worker system.

It keeps the reusable mechanics:

- durable `jobs` plus `job_events`
- idempotent create-or-reuse by `scope_key + client_token`
- one-active-job-per-scope enforcement
- configurable per-owner active-job caps
- worker claiming with leases and heartbeats
- stale-worker protection on lease-owned writes
- durable event timelines with replay-friendly ordering
- immediate live fanout over Redis with Postgres as the source of truth
- checkpointed streaming text buffers
- pause, resume, cancel, fail, and complete transitions

It intentionally leaves out app-specific behavior:

- chat message persistence
- router/model execution
- frontend SSE routes
- project/user/chat schema from AI Linux Assistant
- MAGI-specific role semantics

## Package Layout

- `durable_compute_plane/models.py`
  - SQLAlchemy models for generic jobs and job events
- `durable_compute_plane/store.py`
  - durable store, claim logic, leases, events, terminal transitions
- `durable_compute_plane/worker.py`
  - reusable worker service and execution context
- `durable_compute_plane/buffers.py`
  - live delta plus durable checkpoint buffering
- `durable_compute_plane/redis.py`
  - Redis pub/sub helpers
- `durable_compute_plane/event_serializer.py`
  - shared wire serializer for live and replayed events

## Mapping From AI Linux Assistant

The extracted package was derived primarily from:

- `Back-end/app/chat_run_worker.py`
- `Back-end/app/persistence/postgres_run_store.py`
- `Back-end/app/streaming/redis_events.py`
- `Back-end/app/streaming/event_serializer.py`
- `Back-end/app/orchestration/RUNS.md`

The current project-specific equivalents map like this:

- `ChatRun` -> `ComputeJob`
- `ChatRunEvent` -> `ComputeJobEvent`
- `PostgresRunStore` -> `ComputePlaneStore`
- `ChatRunWorkerService` -> `LeasedJobWorkerService`
- `pause_state_json` -> `checkpoint_state_json`
- `partial_assistant_text` -> `partial_output_text`

## Minimal Usage

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from durable_compute_plane.models import Base
from durable_compute_plane.store import ComputePlaneStore
from durable_compute_plane.worker import (
    JobExecutionResult,
    LeasedJobWorkerService,
    WorkerSettings,
)


engine = create_engine("postgresql+psycopg://...")
Base.metadata.create_all(engine)
session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
store = ComputePlaneStore(session_factory=session_factory)


class DemoWorker(LeasedJobWorkerService):
    def execute_job(self, job, context):
        context.emit_state("START")
        context.push_output_delta("working...")
        context.emit_event("demo_step", {"job_id": job.id})
        return JobExecutionResult(
            result_json={"status": "ok"},
            done_payload={"result": {"status": "ok"}},
        )


worker = DemoWorker(store=store, settings=WorkerSettings(concurrency=1))
worker.run()
```

## Design Notes

- Postgres is still the authority for replay and recovery.
- Redis is live fanout only.
- Worker-owned writes require a valid active lease.
- Resume support is intentionally generic: callers own the contents of `checkpoint_state_json`.
- The base package keeps the current project's checkpointed text-streaming pattern, but generalizes it to named scoped streams as well.

## Running Tests

```bash
python -m pytest tests/
```
