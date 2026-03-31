import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class IngestDocumentTrace:
    queue_index: int
    queue_total: int
    source_path: str
    filename: str
    status: str
    state_trace: list[str]
    raw_elements: int
    cleaned_elements: int
    started_at: str
    completed_at: str
    duration_seconds: float
    artifacts: dict[str, Any] = field(default_factory=dict)
    archived_to: str | None = None
    error: str | None = None


class IngestTraceRecorder:
    def __init__(
        self,
        trace_output_dir: Path,
        mode: str,
        target_path: Path,
        total_documents: int,
        config: dict[str, Any],
    ) -> None:
        self.trace_output_dir = trace_output_dir
        self.trace_output_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = _build_run_id()
        self.started_at = _utc_now_iso()
        self.mode = mode
        self.target_path = str(target_path)
        self.total_documents = total_documents
        self.config = config
        self.documents: list[IngestDocumentTrace] = []
        self.completed_documents = 0
        self.failed_documents = 0
        self.trace_path = self._build_trace_path()

    def _build_trace_path(self) -> Path:
        candidate = self.trace_output_dir / f"ingest_run_{self.run_id}.json"
        if not candidate.exists():
            return candidate

        counter = 1
        while True:
            candidate = self.trace_output_dir / f"ingest_run_{self.run_id}_{counter}.json"
            if not candidate.exists():
                return candidate
            counter += 1

    def record_document(
        self,
        *,
        queue_index: int,
        queue_total: int,
        source_path: Path,
        filename: str,
        status: str,
        state_trace: list[str],
        raw_elements: int,
        cleaned_elements: int,
        started_at: str,
        completed_at: str,
        duration_seconds: float,
        artifacts: dict[str, Any],
        archived_to: Path | None = None,
        error: str | None = None,
    ) -> None:
        if status == "completed":
            self.completed_documents += 1
        else:
            self.failed_documents += 1

        self.documents.append(
            IngestDocumentTrace(
                queue_index=queue_index,
                queue_total=queue_total,
                source_path=str(source_path),
                filename=filename,
                status=status,
                state_trace=state_trace,
                raw_elements=raw_elements,
                cleaned_elements=cleaned_elements,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration_seconds,
                artifacts=artifacts,
                archived_to=str(archived_to) if archived_to else None,
                error=error,
            )
        )
        self.write()

    def write(self) -> None:
        payload = {
            "run_id": self.run_id,
            "mode": self.mode,
            "target_path": self.target_path,
            "started_at": self.started_at,
            "updated_at": _utc_now_iso(),
            "total_documents": self.total_documents,
            "completed_documents": self.completed_documents,
            "failed_documents": self.failed_documents,
            "config": self.config,
            "documents": [asdict(document) for document in self.documents],
        }
        self.trace_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
