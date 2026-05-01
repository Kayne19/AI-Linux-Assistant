"""Artifact export routes."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import StoreDep
from ..schemas import ArtifactExportRequest

router = APIRouter(tags=["artifacts"])


@router.post("/benchmarks/{benchmark_id}/export-artifacts")
def export_benchmark_artifacts(
    benchmark_id: str,
    store: StoreDep,
    body: ArtifactExportRequest | None = None,
    backend_name: str = Query(default="aws_ec2"),
    controller_name: str = Query(default="ssm"),
) -> dict:
    """Export a benchmark run's artifacts via PostgresArtifactExporter.

    Returns the full ArtifactPack as a JSON-serializable dict.
    If `artifacts_root` is provided in the request body, also saves the pack
    to disk at `{artifacts_root}/{benchmark_id}/artifact-pack.json`.
    """
    from eval_harness.artifacts import ArtifactStore, PostgresArtifactExporter

    exporter = PostgresArtifactExporter(store)
    pack = exporter.export_benchmark_run(
        benchmark_id,
        backend_name=backend_name,
        controller_name=controller_name,
    )

    result = pack.to_dict()

    # Optionally persist to disk
    if body and body.artifacts_root:
        artifact_store = ArtifactStore(body.artifacts_root)
        saved_path = artifact_store.save_pack(pack, export_id=benchmark_id)
        result["saved_path"] = str(saved_path)

    return result
