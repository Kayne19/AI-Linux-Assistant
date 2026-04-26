from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

SRC_EVAL_HARNESS = Path(__file__).resolve().parents[1] / "src" / "eval_harness"
if "eval_harness" not in sys.modules:
    namespace_pkg = ModuleType("eval_harness")
    namespace_pkg.__path__ = [str(SRC_EVAL_HARNESS)]  # type: ignore[attr-defined]
    sys.modules["eval_harness"] = namespace_pkg

from eval_harness.judges.base import BlindJudge
from eval_harness.models import (
    BlindJudgeRequest,
    BlindJudgeResult,
    PairwiseJudgeRequest,
    PairwiseJudgeResult,
    PairwiseVerdict,
)
from eval_harness.orchestration.judge import JudgeJobOrchestrator
from eval_harness.persistence.database import build_engine, build_session_factory, create_all_tables
from eval_harness.persistence.store import EvalHarnessStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_store() -> EvalHarnessStore:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    create_all_tables(engine)
    return EvalHarnessStore(build_session_factory(engine))


def _make_scenario_and_benchmark(
    store: EvalHarnessStore,
    subject_names: list[str],
    repair_successes: list[bool | None] | None = None,
) -> tuple[object, object, list[object]]:
    """Create a full scenario→revision→setup→benchmark→evaluation_runs fixture."""
    scenario = store.create_scenario(title="Nginx Repair", scenario_name_hint="nginx-repair")
    revision = store.create_scenario_revision(
        scenario_id=scenario.id,
        target_image="ami-golden",
        summary="Recover nginx",
        what_it_tests={"items": ["systemd recovery"]},
        observable_problem_statement="nginx is down",
        sabotage_plan={"steps": ["break it"]},
        verification_plan={"probes": [{"name": "broken", "command": "true", "expected_exit_code": 0}]},
        judge_rubric={"items": ["diagnosis", "actionability"]},
        planner_metadata={"repair_checks": [], "turn_budget": 2},
    )
    setup = store.create_setup_run(scenario_revision_id=revision.id, status="running")
    store.update_setup_run_status(
        setup_run_id=setup.id,
        status="verified",
        broken_image_id="ami-broken",
        planner_approved=True,
    )
    subjects = []
    for name in subject_names:
        s = store.upsert_subject(
            subject_name=name,
            adapter_type="fake",
            display_name=name,
            adapter_config={},
        )
        subjects.append(s)
    benchmark = store.create_benchmark_run(
        scenario_revision_id=revision.id,
        verified_setup_run_id=setup.id,
        subject_ids=[s.id for s in subjects],
    )
    repair_successes = repair_successes or [None] * len(subject_names)
    eval_runs = []
    for s, repair in zip(subjects, repair_successes):
        er = store.create_evaluation_run(
            benchmark_run_id=benchmark.id,
            subject_id=s.id,
            status="completed",
        )
        if repair is not None:
            store.update_evaluation_run_status(
                evaluation_run_id=er.id,
                status="completed",
                repair_success=repair,
                finished=True,
            )
        eval_runs.append(er)
    return revision, benchmark, eval_runs


# ---------------------------------------------------------------------------
# FakeJudge variants
# ---------------------------------------------------------------------------

@dataclass
class FakeJudge(BlindJudge):
    name: str = "fake_judge"
    requests: list[BlindJudgeRequest] = field(default_factory=list)
    scripted_score: int = 3

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        self.requests.append(request)
        return BlindJudgeResult(
            blind_label=request.blind_label,
            summary="ok",
            scores={
                crit: {"score": self.scripted_score, "rationale": "fine", "evidence": ""}
                for crit in request.rubric
            },
            raw_response={},
        )

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        verdicts = tuple(
            PairwiseVerdict(
                criterion=crit,
                winner="A",
                margin="slight",
                rationale="A was better",
                evidence_a="",
                evidence_b="",
            )
            for crit in request.rubric
        )
        return PairwiseJudgeResult(
            blind_label_a=request.blind_label_a,
            blind_label_b=request.blind_label_b,
            summary="A wins",
            verdicts=verdicts,
            raw_response={},
        )


@dataclass
class ScriptedPairwiseJudge(BlindJudge):
    """Returns scripted verdicts from a list in round-robin order."""

    name: str = "scripted_pairwise_judge"
    compare_responses: list[PairwiseJudgeResult] = field(default_factory=list)
    compare_calls: list[PairwiseJudgeRequest] = field(default_factory=list)
    _call_idx: int = field(default=0, init=False, repr=False)

    def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
        return BlindJudgeResult(blind_label=request.blind_label, summary="", scores={}, raw_response={})

    def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
        self.compare_calls.append(request)
        if not self.compare_responses:
            return PairwiseJudgeResult(
                blind_label_a=request.blind_label_a,
                blind_label_b=request.blind_label_b,
                summary="",
                verdicts=(),
                raw_response={},
            )
        idx = self._call_idx % len(self.compare_responses)
        object.__setattr__(self, "_call_idx", self._call_idx + 1)
        return self.compare_responses[idx]


# ---------------------------------------------------------------------------
# run_absolute tests
# ---------------------------------------------------------------------------

def test_run_absolute_populates_repair_success() -> None:
    """run_absolute passes repair_success from EvaluationRun to the judge request."""
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(
        store, ["subj-a"], repair_successes=[True]
    )
    judge = FakeJudge()
    JudgeJobOrchestrator(judge=judge, store=store).run_absolute(benchmark_run_id=benchmark.id)
    assert len(judge.requests) == 1
    assert judge.requests[0].repair_success is True


def test_run_absolute_none_repair_success() -> None:
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["subj-a"], repair_successes=[None])
    judge = FakeJudge()
    JudgeJobOrchestrator(judge=judge, store=store).run_absolute(benchmark_run_id=benchmark.id)
    assert judge.requests[0].repair_success is None


def test_run_absolute_single_judge_aggregate_equals_judge_score() -> None:
    """Single judge → aggregate row score == judge score."""
    store = _build_store()
    _, benchmark, eval_runs = _make_scenario_and_benchmark(
        store, ["subj-a"], repair_successes=[True]
    )
    judge = FakeJudge(scripted_score=3)
    result = JudgeJobOrchestrator(judge=judge, store=store).run_absolute(benchmark_run_id=benchmark.id)
    items = store.list_judge_items(result.judge_job_id)
    agg_items = [i for i in items if i.kind == "absolute_aggregate"]
    assert len(agg_items) == 1
    agg_scores = agg_items[0].parsed_scores_json
    for crit_data in agg_scores.values():
        assert crit_data["score"] == 3.0


def test_run_absolute_multi_judge_weighted_mean() -> None:
    """Two judges with different scores → aggregate is weighted mean."""
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["subj-a"], repair_successes=[False])
    judge_a = FakeJudge(name="judge_a", scripted_score=2)
    judge_b = FakeJudge(name="judge_b", scripted_score=4)
    # Equal weights → mean = 3.
    result = JudgeJobOrchestrator(
        judges=[judge_a, judge_b], store=store
    ).run_absolute(benchmark_run_id=benchmark.id)
    items = store.list_judge_items(result.judge_job_id)
    agg_items = [i for i in items if i.kind == "absolute_aggregate"]
    assert len(agg_items) == 1
    for crit_data in agg_items[0].parsed_scores_json.values():
        assert abs(crit_data["score"] - 3.0) < 1e-6


def test_run_absolute_multi_judge_weighted_mean_unequal_weights() -> None:
    """Unequal weights: w1=1, w2=3 → mean = (2*1 + 4*3)/4 = 3.5."""
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["subj-a"])
    judge_a = FakeJudge(name="judge_a", scripted_score=2)
    judge_b = FakeJudge(name="judge_b", scripted_score=4)
    result = JudgeJobOrchestrator(
        judges=[judge_a, judge_b], store=store, weights=[1.0, 3.0]
    ).run_absolute(benchmark_run_id=benchmark.id)
    items = store.list_judge_items(result.judge_job_id)
    agg_items = [i for i in items if i.kind == "absolute_aggregate"]
    for crit_data in agg_items[0].parsed_scores_json.values():
        assert abs(crit_data["score"] - 3.5) < 1e-6


def test_run_absolute_multi_judge_produces_per_judge_and_agg_rows() -> None:
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["subj-a", "subj-b"])
    judge_a = FakeJudge(name="ja")
    judge_b = FakeJudge(name="jb")
    result = JudgeJobOrchestrator(judges=[judge_a, judge_b], store=store).run_absolute(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    absolute_items = [i for i in items if i.kind == "absolute"]
    agg_items = [i for i in items if i.kind == "absolute_aggregate"]
    # 2 subjects × 2 judges = 4 absolute rows; 2 subjects × 1 aggregate = 2 agg rows.
    assert len(absolute_items) == 4
    assert len(agg_items) == 2


def test_run_backward_compat_legacy_run() -> None:
    """Legacy run() delegates to run_absolute."""
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["subj-a"], repair_successes=[True])
    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run(benchmark_run_id=benchmark.id)
    assert result.judge_job_id
    assert judge.requests[0].repair_success is True


# ---------------------------------------------------------------------------
# run_pairwise tests
# ---------------------------------------------------------------------------

def test_run_pairwise_4_subjects_6_pairs() -> None:
    """4 subjects → 6 unordered pairs → 2 ordered calls each → 12 pairwise rows per judge."""
    store = _build_store()
    names = ["alpha", "beta", "gamma", "delta"]
    _, benchmark, _ = _make_scenario_and_benchmark(store, names)
    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run_pairwise(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    pairwise_raw = [i for i in items if i.kind == "pairwise"]
    pairwise_merged = [i for i in items if i.kind == "pairwise_merged"]
    pairwise_agg = [i for i in items if i.kind == "pairwise_aggregate"]
    bt_items = [i for i in items if i.kind == "pairwise_bt"]
    # 6 pairs × 2 orderings = 12 raw rows per judge (1 judge).
    assert len(pairwise_raw) == 12
    # 6 pairs × 1 merge-per-judge × 1 judge = 6.
    assert len(pairwise_merged) == 6
    # 6 pairs × 1 agg.
    assert len(pairwise_agg) == 6
    # 1 BT row.
    assert len(bt_items) == 1


def test_run_pairwise_bt_row_has_all_subjects() -> None:
    store = _build_store()
    names = ["alpha", "beta", "gamma"]
    _, benchmark, _ = _make_scenario_and_benchmark(store, names)
    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run_pairwise(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    bt_item = next(i for i in items if i.kind == "pairwise_bt")
    bt_scores = bt_item.parsed_scores_json
    assert set(bt_scores.keys()) == set(names)
    for subj, data in bt_scores.items():
        assert "rating" in data


def test_run_pairwise_anchor_subject_restricts_pairs() -> None:
    """anchor_subject='alpha' → 3 pairs (alpha vs beta, gamma, delta)."""
    store = _build_store()
    names = ["alpha", "beta", "gamma", "delta"]
    _, benchmark, _ = _make_scenario_and_benchmark(store, names)
    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run_pairwise(
        benchmark_run_id=benchmark.id, anchor_subject="alpha"
    )
    items = store.list_judge_items(result.judge_job_id)
    pairwise_raw = [i for i in items if i.kind == "pairwise"]
    # 3 pairs × 2 orderings = 6.
    assert len(pairwise_raw) == 6


def test_run_pairwise_pairwise_rows_have_eval_run_ids() -> None:
    """Pairwise rows must have evaluation_run_id_a and _b set, not evaluation_run_id."""
    store = _build_store()
    _, benchmark, eval_runs = _make_scenario_and_benchmark(store, ["alpha", "beta"])
    judge = FakeJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run_pairwise(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    pairwise_raw = [i for i in items if i.kind == "pairwise"]
    for item in pairwise_raw:
        assert item.evaluation_run_id is None
        assert item.evaluation_run_id_a is not None
        assert item.evaluation_run_id_b is not None


def test_run_pairwise_multi_judge_rows() -> None:
    """N judges → N × 2 raw rows per pair + N merged rows + 1 aggregate row."""
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(store, ["alpha", "beta"])
    judge_a = FakeJudge(name="ja")
    judge_b = FakeJudge(name="jb")
    result = JudgeJobOrchestrator(judges=[judge_a, judge_b], store=store).run_pairwise(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    pairwise_raw = [i for i in items if i.kind == "pairwise"]
    pairwise_merged = [i for i in items if i.kind == "pairwise_merged"]
    pairwise_agg = [i for i in items if i.kind == "pairwise_aggregate"]
    # 1 pair × 2 judges × 2 orderings = 4 raw.
    assert len(pairwise_raw) == 4
    # 1 pair × 2 judges = 2 merged.
    assert len(pairwise_merged) == 2
    # 1 pair × 1 aggregate.
    assert len(pairwise_agg) == 1


# ---------------------------------------------------------------------------
# Order-swap merge tests
# ---------------------------------------------------------------------------

from eval_harness.orchestration.judge import _merge_verdicts


def _make_result(
    verdicts: list[tuple[str, str, str]],
    *,
    label_a: str = "A",
    label_b: str = "B",
) -> PairwiseJudgeResult:
    return PairwiseJudgeResult(
        blind_label_a=label_a,
        blind_label_b=label_b,
        summary="",
        verdicts=tuple(
            PairwiseVerdict(
                criterion=crit, winner=winner, margin=margin,
                rationale="", evidence_a="", evidence_b="",
            )
            for crit, winner, margin in verdicts
        ),
        raw_response={},
    )


def test_merge_agree_forward_winner_a() -> None:
    """Both orderings agree A wins → merged winner is A."""
    fwd = _make_result([("Diagnosis correctness", "A", "clear")])
    # Backward: A=B_original, so winner="B" in backward means B_original (= A_original) wins.
    bwd = _make_result([("Diagnosis correctness", "B", "slight")])
    merged = _merge_verdicts(fwd, bwd, repair_success_a=None, repair_success_b=None)
    assert merged[0].winner == "A"
    assert merged[0].margin == "slight"  # conservative.


def test_merge_disagree_produces_tie() -> None:
    """Forward says A wins, backward says A wins in B's slot → disagree → tie."""
    fwd = _make_result([("Diagnosis correctness", "A", "clear")])
    bwd = _make_result([("Diagnosis correctness", "A", "clear")])
    # fwd winner=A; bwd winner=A means original_B wins → disagree → tie.
    merged = _merge_verdicts(fwd, bwd, repair_success_a=None, repair_success_b=None)
    assert merged[0].winner == "tie"


def test_merge_outcome_mechanical_a_wins() -> None:
    """repair_success_a=True, repair_success_b=False → Outcome winner=A decisively."""
    fwd = _make_result([("Outcome — did the system end up repaired?", "B", "clear")])
    bwd = _make_result([("Outcome — did the system end up repaired?", "B", "clear")])
    merged = _merge_verdicts(fwd, bwd, repair_success_a=True, repair_success_b=False)
    outcome = merged[0]
    assert outcome.winner == "A"
    assert outcome.margin == "decisive"


def test_merge_outcome_mechanical_b_wins() -> None:
    fwd = _make_result([("Outcome — did the system end up repaired?", "A", "clear")])
    bwd = _make_result([("Outcome — did the system end up repaired?", "A", "clear")])
    merged = _merge_verdicts(fwd, bwd, repair_success_a=False, repair_success_b=True)
    assert merged[0].winner == "B"


def test_merge_outcome_both_succeed_tie() -> None:
    fwd = _make_result([("Outcome — did the system end up repaired?", "A", "decisive")])
    bwd = _make_result([("Outcome — did the system end up repaired?", "B", "decisive")])
    merged = _merge_verdicts(fwd, bwd, repair_success_a=True, repair_success_b=True)
    assert merged[0].winner == "tie"


def test_pairwise_orchestrator_mechanical_outcome_override() -> None:
    """
    Orchestrator must enforce mechanical Outcome override even if judges disagree.
    repair_success_a=True, repair_success_b=False → Outcome must be A wins decisively.
    """
    store = _build_store()
    _, benchmark, _ = _make_scenario_and_benchmark(
        store, ["alpha", "beta"], repair_successes=[True, False]
    )
    # Judge always says B wins on all criteria (including Outcome) — should be overridden.
    @dataclass
    class OutcomeWrongJudge(BlindJudge):
        name: str = "outcome_wrong_judge"

        def grade(self, request: BlindJudgeRequest) -> BlindJudgeResult:
            return BlindJudgeResult(blind_label=request.blind_label, summary="", scores={}, raw_response={})

        def compare(self, request: PairwiseJudgeRequest) -> PairwiseJudgeResult:
            return PairwiseJudgeResult(
                blind_label_a=request.blind_label_a,
                blind_label_b=request.blind_label_b,
                summary="",
                verdicts=tuple(
                    PairwiseVerdict(
                        criterion=crit, winner="B", margin="decisive",
                        rationale="judge says B", evidence_a="", evidence_b="",
                    )
                    for crit in request.rubric
                ),
                raw_response={},
            )

    judge = OutcomeWrongJudge()
    result = JudgeJobOrchestrator(judge=judge, store=store).run_pairwise(
        benchmark_run_id=benchmark.id
    )
    items = store.list_judge_items(result.judge_job_id)
    agg_items = [i for i in items if i.kind == "pairwise_aggregate"]
    assert len(agg_items) == 1
    agg_verdicts = agg_items[0].parsed_scores_json.get("verdicts", [])
    outcome_verdicts = [v for v in agg_verdicts if "Outcome" in v.get("criterion", "")]
    if outcome_verdicts:
        assert outcome_verdicts[0]["winner"] == "A"
        assert outcome_verdicts[0]["margin"] == "decisive"
