from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import select

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.persistence.database import build_engine, build_session_factory, create_all_tables
from eval_harness.persistence.postgres_models import (
    EvaluationEventRecord,
    ScenarioRecord,
    ScenarioRevisionRecord,
    ScenarioSetupEventRecord,
)
from eval_harness.persistence.store import EvalHarnessStore, normalize_scenario_name


def _build_store() -> tuple[EvalHarnessStore, object]:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    session_factory = build_session_factory(engine)
    return EvalHarnessStore(session_factory), session_factory


def test_normalize_scenario_name() -> None:
    assert normalize_scenario_name("  Nginx Service Repair  ") == "nginx-service-repair"
    assert normalize_scenario_name("%%%") == "scenario"


def test_scenario_name_allocation_and_revision_increment() -> None:
    store, session_factory = _build_store()
    first = store.create_scenario(title="Nginx Repair", scenario_name_hint="Nginx Repair")
    second = store.create_scenario(title="Nginx Repair v2", scenario_name_hint="Nginx Repair")
    assert first.scenario_name == "nginx-repair"
    assert second.scenario_name == "nginx-repair-2"

    rev1 = store.create_scenario_revision(
        scenario_id=first.id,
        target_image="ami-1",
        summary="summary",
        what_it_tests={"category": "service-recovery"},
        observable_problem_statement="nginx down",
        sabotage_plan={"steps": ["disable service"]},
        verification_plan={"probes": [{"command": "systemctl status nginx"}]},
        judge_rubric={"criteria": ["accuracy"]},
    )
    rev2 = store.create_scenario_revision(
        scenario_id=first.id,
        target_image="ami-1",
        summary="summary 2",
        what_it_tests={"category": "service-recovery"},
        observable_problem_statement="nginx down",
        sabotage_plan={"steps": ["disable service"]},
        verification_plan={"probes": [{"command": "systemctl status nginx"}]},
        judge_rubric={"criteria": ["accuracy"]},
    )
    assert rev1.revision_number == 1
    assert rev2.revision_number == 2

    store.mark_scenario_verified(scenario_id=first.id, revision_id=rev2.id)
    with session_factory() as session:
        row = session.get(ScenarioRecord, first.id)
        assert row is not None
        assert row.current_verified_revision_id == rev2.id
        assert row.verification_status == "verified"


def test_setup_benchmark_evaluation_and_judge_records() -> None:
    store, session_factory = _build_store()
    scenario = store.create_scenario(title="Filesystem Permissions", scenario_name_hint="perm test")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-2",
        summary="permissions issue",
        what_it_tests={"category": "permissions"},
        observable_problem_statement="app cannot write temp file",
        sabotage_plan={"steps": ["chmod 000 /var/tmp/app"]},
        verification_plan={"probes": [{"command": "ls -ld /var/tmp/app"}]},
        judge_rubric={"criteria": ["diagnosis", "repair"]},
    )

    setup = store.create_setup_run(scenario_revision_id=revision.id, staging_handle_id="i-staging")
    event = store.append_setup_event(
        setup_run_id=setup.id,
        round_index=0,
        seq=1,
        actor_role="sabotage_agent",
        event_kind="command_result",
        payload={"stdout": "permission denied", "exit_code": 1},
    )
    assert event.setup_run_id == setup.id
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
        backend_metadata={"resolved_golden_ami_id": "ami-golden", "golden_image_build_triggered": False},
    )
    store.mark_scenario_verified(scenario_id=scenario.id, revision_id=revision.id)

    subject = store.upsert_subject(
        subject_name="system-a",
        adapter_type="http",
        display_name="System A",
        adapter_config={"base_url": "http://localhost"},
    )
    benchmark = store.create_benchmark_run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        subject_ids=[subject.id],
    )
    evaluation = store.create_evaluation_run(
        benchmark_run_id=benchmark.id,
        subject_id=subject.id,
        clone_handle_id="i-clone",
    )
    eval_event = store.append_evaluation_event(
        evaluation_run_id=evaluation.id,
        seq=1,
        actor_role="user_proxy",
        event_kind="message",
        payload={"content": "service is down"},
    )
    assert eval_event.evaluation_run_id == evaluation.id
    store.update_evaluation_run_status(
        evaluation_run_id=evaluation.id,
        status="completed",
        repair_success=True,
        resolution_result={"checks_passed": True},
        finished=True,
    )

    judge_job = store.create_judge_job(
        benchmark_run_id=benchmark.id,
        judge_adapter_type="blind_llm",
        rubric={"criteria": ["helpfulness"]},
    )
    item = store.create_judge_item(
        judge_job_id=judge_job.id,
        evaluation_run_id=evaluation.id,
        blind_label="candidate-1",
        blinded_transcript={"turns": [{"role": "assistant", "content": "try systemctl restart"}]},
        parsed_scores={"helpfulness": 4},
        summary="clear and actionable",
    )
    assert item.blind_label == "candidate-1"

    with session_factory() as session:
        setup_events = session.scalars(
            select(ScenarioSetupEventRecord).where(ScenarioSetupEventRecord.setup_run_id == setup.id)
        ).all()
        eval_events = session.scalars(
            select(EvaluationEventRecord).where(EvaluationEventRecord.evaluation_run_id == evaluation.id)
        ).all()
        revisions = session.scalars(
            select(ScenarioRevisionRecord).where(ScenarioRevisionRecord.scenario_id == scenario.id)
        ).all()

    assert len(setup_events) == 1
    assert len(eval_events) == 1
    assert len(revisions) == 1


def test_mark_scenario_verified_rejects_revision_from_other_scenario() -> None:
    store, _ = _build_store()
    first = store.create_scenario(title="First", scenario_name_hint="first")
    second = store.create_scenario(title="Second", scenario_name_hint="second")
    second_revision = store.create_scenario_revision(
        scenario_id=second.id,
        target_image="ami-other",
        summary="other",
        what_it_tests={"category": "other"},
        observable_problem_statement="broken",
        sabotage_plan={"steps": ["x"]},
        verification_plan={"probes": [{"command": "true"}]},
        judge_rubric={"criteria": ["x"]},
    )
    with pytest.raises(ValueError):
        store.mark_scenario_verified(scenario_id=first.id, revision_id=second_revision.id)


def test_setup_run_status_merges_backend_metadata() -> None:
    store, _ = _build_store()
    scenario = store.create_scenario(title="Metadata", scenario_name_hint="metadata")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-2",
        summary="metadata issue",
        what_it_tests={"category": "metadata"},
        observable_problem_statement="metadata",
        sabotage_plan={"steps": ["noop"]},
        verification_plan={"probes": [{"command": "true"}]},
        judge_rubric={"criteria": ["metadata"]},
    )
    setup = store.create_setup_run(
        scenario_revision_id=revision.id,
        backend_metadata={"group_id": "group-1", "requested_target_image": "debian-12-openclaw-golden"},
    )

    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="running",
        backend_metadata={"resolved_golden_ami_id": "ami-golden", "golden_image_build_triggered": True},
    )

    stored = store.get_setup_run(setup.id)
    assert stored is not None
    assert stored.backend_metadata_json == {
        "group_id": "group-1",
        "requested_target_image": "debian-12-openclaw-golden",
        "resolved_golden_ami_id": "ami-golden",
        "golden_image_build_triggered": True,
    }
