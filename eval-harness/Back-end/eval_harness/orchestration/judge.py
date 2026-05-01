from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..judges.base import BlindJudge
from ..judges.rubric import OUTCOME_CRITERION, UNIVERSAL_RUBRIC, format_tagged_rubric
from ..mapping import turn_record_from_evaluation_event
from ..models import (
    BlindJudgeRequest,
    JudgeJobStatus,
    PairwiseJudgeRequest,
    PairwiseJudgeResult,
    PairwiseVerdict,
)
from ..persistence.store import EvalHarnessStore
from .bradley_terry import bootstrap_bradley_terry, fit_bradley_terry


@dataclass(frozen=True)
class JudgeJobResult:
    judge_job_id: str
    judge_item_ids: tuple[str, ...]


_MARGIN_ORDER = ("slight", "clear", "decisive")


def _merge_verdicts(
    forward: PairwiseJudgeResult,
    backward: PairwiseJudgeResult,
    *,
    repair_success_a: bool | None,
    repair_success_b: bool | None,
) -> tuple[PairwiseVerdict, ...]:
    """Merge forward (A,B) and backward (B,A) verdicts per criterion.

    - Agree → that winner (conservative margin).
    - Disagree → tie.
    - Outcome criterion: mechanical override when exactly one side has repair_success=True.
    """
    fwd_by_criterion = {v.criterion: v for v in forward.verdicts}
    bwd_by_criterion = {v.criterion: v for v in backward.verdicts}
    criteria = list(dict.fromkeys(list(fwd_by_criterion) + list(bwd_by_criterion)))

    merged: list[PairwiseVerdict] = []
    for crit in criteria:
        fv = fwd_by_criterion.get(crit)
        bv = bwd_by_criterion.get(crit)

        # Mechanical outcome override.
        if OUTCOME_CRITERION in crit:
            if repair_success_a is True and repair_success_b is not True:
                merged.append(PairwiseVerdict(
                    criterion=crit, winner="A", margin="decisive",
                    rationale="Mechanical: repair_success_a=True, repair_success_b not True.",
                    evidence_a="", evidence_b="",
                ))
                continue
            elif repair_success_b is True and repair_success_a is not True:
                merged.append(PairwiseVerdict(
                    criterion=crit, winner="B", margin="decisive",
                    rationale="Mechanical: repair_success_b=True, repair_success_a not True.",
                    evidence_a="", evidence_b="",
                ))
                continue
            else:
                merged.append(PairwiseVerdict(
                    criterion=crit, winner="tie", margin="slight",
                    rationale="Mechanical: both or neither succeeded.",
                    evidence_a="", evidence_b="",
                ))
                continue

        if fv is None and bv is None:
            continue

        if fv is None or bv is None:
            v = fv or bv
            assert v is not None
            merged.append(v)
            continue

        # bv is from the (B,A) ordering: winner="A" means B won in original labelling.
        bv_winner_in_ab = {"A": "B", "B": "A", "tie": "tie"}.get(bv.winner, "tie")

        if fv.winner == bv_winner_in_ab:
            conservative_margin = min(
                _MARGIN_ORDER.index(fv.margin),
                _MARGIN_ORDER.index(bv.margin),
            )
            merged.append(PairwiseVerdict(
                criterion=crit,
                winner=fv.winner,
                margin=_MARGIN_ORDER[conservative_margin],
                rationale=fv.rationale,
                evidence_a=fv.evidence_a,
                evidence_b=fv.evidence_b,
            ))
        else:
            merged.append(PairwiseVerdict(
                criterion=crit, winner="tie", margin="slight",
                rationale="Order-swap disagreement resolved as tie.",
                evidence_a=fv.evidence_a,
                evidence_b=fv.evidence_b,
            ))

    return tuple(merged)


def _verdict_weight(winner: str, margin: str) -> float:
    """Fractional win for A given a verdict (A wins fully, B wins 0, tie=0.5)."""
    base = {"A": 1.0, "B": 0.0, "tie": 0.5}.get(winner, 0.5)
    return base


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total_w = sum(weights)
    if total_w == 0.0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _variance(values: list[float], weights: list[float]) -> float:
    mean = _weighted_mean(values, weights)
    total_w = sum(weights)
    if total_w == 0.0 or len(values) < 2:
        return 0.0
    return sum(w * (v - mean) ** 2 for v, w in zip(values, weights)) / total_w


class JudgeJobOrchestrator:
    def __init__(
        self,
        *,
        judge: BlindJudge | None = None,
        judges: Sequence[BlindJudge] | None = None,
        store: EvalHarnessStore,
        weights: Sequence[float] | None = None,
    ) -> None:
        if judges is not None:
            self._judges: tuple[BlindJudge, ...] = tuple(judges)
        elif judge is not None:
            self._judges = (judge,)
        else:
            raise ValueError("Either judge or judges must be provided.")
        if weights is not None:
            if len(weights) != len(self._judges):
                raise ValueError("weights length must match judges length.")
            self._weights: tuple[float, ...] = tuple(weights)
        else:
            self._weights = tuple(1.0 for _ in self._judges)
        self.store = store
        # Back-compat: single-judge attribute.
        self.judge = self._judges[0] if len(self._judges) == 1 else None

    def _build_transcript(self, evaluation_run_id: str):
        turns = []
        for event in self.store.list_evaluation_events(evaluation_run_id):
            turn = turn_record_from_evaluation_event(event)
            if turn is not None:
                turns.append(
                    turn.__class__(
                        role=turn.role,
                        content=turn.content,
                        created_at=turn.created_at,
                        metadata={},
                    )
                )
        return tuple(turns)

    def _get_rubric(
        self,
        revision,
        rubric: tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        scenario_items = tuple(
            str(item)
            for item in (revision.judge_rubric_json or {}).get("items", [])
        )
        return rubric if rubric is not None else format_tagged_rubric(UNIVERSAL_RUBRIC, scenario_items)

    # ------------------------------------------------------------------
    # Absolute grading
    # ------------------------------------------------------------------

    def run_absolute(
        self,
        *,
        benchmark_run_id: str,
        rubric: tuple[str, ...] | None = None,
    ) -> JudgeJobResult:
        benchmark_run = self.store.get_benchmark_run(benchmark_run_id)
        if benchmark_run is None:
            raise ValueError(f"Unknown benchmark run {benchmark_run_id}")
        revision = self.store.get_scenario_revision(benchmark_run.scenario_revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {benchmark_run.scenario_revision_id}")
        rubric_items = self._get_rubric(revision, rubric)
        judge_adapter_type = ",".join(j.name for j in self._judges)
        judge_job = self.store.create_judge_job(
            benchmark_run_id=benchmark_run_id,
            judge_adapter_type=judge_adapter_type,
            rubric={"items": list(rubric_items)},
        )
        self.store.update_judge_job_status(
            judge_job_id=judge_job.id, status=JudgeJobStatus.RUNNING.value, started=True
        )
        item_ids: list[str] = []
        try:
            for index, evaluation_run in enumerate(
                self.store.list_evaluation_runs(benchmark_run_id), start=1
            ):
                blind_label = f"candidate-{index}"
                transcript = self._build_transcript(evaluation_run.id)
                per_judge_scores: list[dict] = []
                per_judge_results = []
                for j_idx, judge in enumerate(self._judges):
                    request = BlindJudgeRequest(
                        blind_label=blind_label,
                        transcript=transcript,
                        rubric=rubric_items,
                        repair_success=evaluation_run.repair_success,
                    )
                    result = judge.grade(request)
                    per_judge_scores.append(result.scores)
                    per_judge_results.append(result)
                    item = self.store.create_judge_item(
                        judge_job_id=judge_job.id,
                        evaluation_run_id=evaluation_run.id,
                        blind_label=f"{blind_label}-judge{j_idx}",
                        blinded_transcript=request.to_dict(),
                        raw_judge_response=result.raw_response,
                        parsed_scores=result.scores,
                        summary=result.summary,
                        kind="absolute",
                        judge_name=judge.name,
                    )
                    item_ids.append(item.id)

                # Aggregate row: weighted mean per criterion.
                agg_scores, agg_metadata = self._aggregate_absolute_scores(
                    per_judge_scores, self._weights
                )
                agg_item = self.store.create_judge_item(
                    judge_job_id=judge_job.id,
                    evaluation_run_id=evaluation_run.id,
                    blind_label=f"{blind_label}-aggregate",
                    blinded_transcript={"blind_label": blind_label},
                    raw_judge_response={},
                    parsed_scores=agg_scores,
                    summary=per_judge_results[0].summary if len(per_judge_results) == 1 else "",
                    kind="absolute_aggregate",
                    judge_name=None,
                )
                item_ids.append(agg_item.id)

            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.COMPLETED.value,
                finished=True,
            )
            return JudgeJobResult(judge_job_id=judge_job.id, judge_item_ids=tuple(item_ids))
        except Exception:
            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.FAILED.value,
                finished=True,
            )
            raise

    def _aggregate_absolute_scores(
        self,
        per_judge_scores: list[dict],
        weights: tuple[float, ...],
    ) -> tuple[dict, dict]:
        if not per_judge_scores:
            return {}, {}
        all_criteria: list[str] = list(
            dict.fromkeys(k for s in per_judge_scores for k in s)
        )
        agg: dict = {}
        meta: dict = {}
        for crit in all_criteria:
            vals: list[float] = []
            ws: list[float] = []
            for s, w in zip(per_judge_scores, weights):
                entry = s.get(crit)
                if entry is None:
                    continue
                score_val = entry["score"] if isinstance(entry, dict) else float(entry)
                vals.append(float(score_val))
                ws.append(w)
            mean = _weighted_mean(vals, ws)
            var = _variance(vals, ws)
            agg[crit] = {"score": mean, "rationale": "", "evidence": ""}
            meta[crit] = {"variance": var}
        return agg, meta

    # ------------------------------------------------------------------
    # Legacy run() — kept for back-compat; delegates to run_absolute.
    # ------------------------------------------------------------------

    def run(
        self,
        *,
        benchmark_run_id: str,
        rubric: tuple[str, ...] | None = None,
    ) -> JudgeJobResult:
        return self.run_absolute(benchmark_run_id=benchmark_run_id, rubric=rubric)

    # ------------------------------------------------------------------
    # Pairwise grading
    # ------------------------------------------------------------------

    def run_pairwise(
        self,
        *,
        benchmark_run_id: str,
        rubric: tuple[str, ...] | None = None,
        anchor_subject: str | None = None,
    ) -> JudgeJobResult:
        benchmark_run = self.store.get_benchmark_run(benchmark_run_id)
        if benchmark_run is None:
            raise ValueError(f"Unknown benchmark run {benchmark_run_id}")
        revision = self.store.get_scenario_revision(benchmark_run.scenario_revision_id)
        if revision is None:
            raise ValueError(f"Unknown scenario revision {benchmark_run.scenario_revision_id}")
        rubric_items = self._get_rubric(revision, rubric)
        judge_adapter_type = ",".join(j.name for j in self._judges)
        judge_job = self.store.create_judge_job(
            benchmark_run_id=benchmark_run_id,
            judge_adapter_type=judge_adapter_type,
            rubric={"items": list(rubric_items)},
        )
        self.store.update_judge_job_status(
            judge_job_id=judge_job.id, status=JudgeJobStatus.RUNNING.value, started=True
        )
        item_ids: list[str] = []
        try:
            # Map subject_id -> evaluation_run.
            eval_runs = self.store.list_evaluation_runs(benchmark_run_id)
            subject_to_eval: dict[str, object] = {er.subject_id: er for er in eval_runs}

            # Fetch subject records to get subject_name.
            subject_records = {}
            for er in eval_runs:
                subj = self.store.get_subject(er.subject_id)
                if subj is not None:
                    subject_records[subj.subject_name] = (subj, er)

            subject_names = sorted(subject_records.keys())
            if anchor_subject is not None and anchor_subject not in subject_records:
                raise ValueError(f"anchor_subject {anchor_subject!r} not in benchmark run subjects")

            # Build unordered pairs.
            if anchor_subject is not None:
                pairs_unordered = [
                    (anchor_subject, other)
                    for other in subject_names
                    if other != anchor_subject
                ]
            else:
                pairs_unordered = list(itertools.combinations(subject_names, 2))

            # Per-criterion BT input: list of (winner_subj, loser_subj, weight) per scenario.
            # Grouped by pair for bootstrap.
            bt_groups: list[list[tuple[str, str, float]]] = []

            for name_a, name_b in pairs_unordered:
                _, eval_a = subject_records[name_a]
                _, eval_b = subject_records[name_b]
                transcript_a = self._build_transcript(eval_a.id)
                transcript_b = self._build_transcript(eval_b.id)
                repair_a: bool | None = eval_a.repair_success
                repair_b: bool | None = eval_b.repair_success

                pair_group: list[tuple[str, str, float]] = []
                per_judge_merged: list[tuple[PairwiseVerdict, ...]] = []

                for j_idx, judge in enumerate(self._judges):
                    fwd_req = PairwiseJudgeRequest(
                        blind_label_a="A",
                        blind_label_b="B",
                        transcript_a=transcript_a,
                        transcript_b=transcript_b,
                        rubric=rubric_items,
                        repair_success_a=repair_a,
                        repair_success_b=repair_b,
                    )
                    bwd_req = PairwiseJudgeRequest(
                        blind_label_a="A",
                        blind_label_b="B",
                        transcript_a=transcript_b,
                        transcript_b=transcript_a,
                        rubric=rubric_items,
                        repair_success_a=repair_b,
                        repair_success_b=repair_a,
                    )
                    fwd_result = judge.compare(fwd_req)
                    bwd_result = judge.compare(bwd_req)

                    # Persist raw ordered rows.
                    fwd_label = f"{name_a}-vs-{name_b}-fwd-judge{j_idx}"
                    bwd_label = f"{name_a}-vs-{name_b}-bwd-judge{j_idx}"
                    fwd_item = self.store.create_judge_item(
                        judge_job_id=judge_job.id,
                        evaluation_run_id=None,
                        blind_label=fwd_label,
                        blinded_transcript=fwd_req.to_dict(),
                        raw_judge_response=fwd_result.raw_response,
                        parsed_scores={"verdicts": [v.to_dict() for v in fwd_result.verdicts]},
                        summary=fwd_result.summary,
                        kind="pairwise",
                        judge_name=judge.name,
                        evaluation_run_id_a=eval_a.id,
                        evaluation_run_id_b=eval_b.id,
                    )
                    bwd_item = self.store.create_judge_item(
                        judge_job_id=judge_job.id,
                        evaluation_run_id=None,
                        blind_label=bwd_label,
                        blinded_transcript=bwd_req.to_dict(),
                        raw_judge_response=bwd_result.raw_response,
                        parsed_scores={"verdicts": [v.to_dict() for v in bwd_result.verdicts]},
                        summary=bwd_result.summary,
                        kind="pairwise",
                        judge_name=judge.name,
                        evaluation_run_id_a=eval_b.id,
                        evaluation_run_id_b=eval_a.id,
                    )
                    item_ids.extend([fwd_item.id, bwd_item.id])

                    # Merge forward + backward.
                    merged_verdicts = _merge_verdicts(
                        fwd_result, bwd_result,
                        repair_success_a=repair_a,
                        repair_success_b=repair_b,
                    )
                    per_judge_merged.append(merged_verdicts)

                    merged_label = f"{name_a}-vs-{name_b}-merged-judge{j_idx}"
                    merged_item = self.store.create_judge_item(
                        judge_job_id=judge_job.id,
                        evaluation_run_id=None,
                        blind_label=merged_label,
                        blinded_transcript={},
                        raw_judge_response={},
                        parsed_scores={"verdicts": [v.to_dict() for v in merged_verdicts]},
                        summary="",
                        kind="pairwise_merged",
                        judge_name=judge.name,
                        evaluation_run_id_a=eval_a.id,
                        evaluation_run_id_b=eval_b.id,
                    )
                    item_ids.append(merged_item.id)

                # Multi-judge fractional-win aggregation across all judges.
                agg_verdicts = self._aggregate_pairwise_verdicts(per_judge_merged, self._weights)
                agg_label = f"{name_a}-vs-{name_b}-aggregate"
                agg_item = self.store.create_judge_item(
                    judge_job_id=judge_job.id,
                    evaluation_run_id=None,
                    blind_label=agg_label,
                    blinded_transcript={},
                    raw_judge_response={},
                    parsed_scores={"verdicts": [v.to_dict() for v in agg_verdicts]},
                    summary="",
                    kind="pairwise_aggregate",
                    judge_name=None,
                    evaluation_run_id_a=eval_a.id,
                    evaluation_run_id_b=eval_b.id,
                )
                item_ids.append(agg_item.id)

                # Collect BT data from aggregate verdicts.
                for v in agg_verdicts:
                    w = _verdict_weight(v.winner, v.margin)
                    if w > 0.5:
                        pair_group.append((name_a, name_b, w))
                    elif w < 0.5:
                        pair_group.append((name_b, name_a, 1.0 - w))
                    else:
                        pair_group.append((name_a, name_b, 0.5))

                bt_groups.append(pair_group)

            # Fit Bradley-Terry.
            all_bt_pairs = [p for g in bt_groups for p in g]
            bt_ratings = fit_bradley_terry(all_bt_pairs)
            bt_ci = bootstrap_bradley_terry(bt_groups)
            bt_parsed: dict = {
                subj: {"rating": bt_ratings.get(subj, 0.0), **bt_ci.get(subj, {})}
                for subj in subject_names
            }
            bt_item = self.store.create_judge_item(
                judge_job_id=judge_job.id,
                evaluation_run_id=None,
                blind_label="bradley-terry",
                blinded_transcript={},
                raw_judge_response={},
                parsed_scores=bt_parsed,
                summary="",
                kind="pairwise_bt",
                judge_name=None,
            )
            item_ids.append(bt_item.id)

            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.COMPLETED.value,
                finished=True,
            )
            return JudgeJobResult(judge_job_id=judge_job.id, judge_item_ids=tuple(item_ids))
        except Exception:
            self.store.update_judge_job_status(
                judge_job_id=judge_job.id,
                status=JudgeJobStatus.FAILED.value,
                finished=True,
            )
            raise

    def _aggregate_pairwise_verdicts(
        self,
        per_judge_verdicts: list[tuple[PairwiseVerdict, ...]],
        weights: tuple[float, ...],
    ) -> tuple[PairwiseVerdict, ...]:
        """Fractional-win aggregation across judges per criterion."""
        if not per_judge_verdicts:
            return ()
        all_criteria: list[str] = list(
            dict.fromkeys(v.criterion for verdicts in per_judge_verdicts for v in verdicts)
        )
        result: list[PairwiseVerdict] = []
        for crit in all_criteria:
            frac_a = 0.0
            total_w = 0.0
            for verdicts, w in zip(per_judge_verdicts, weights):
                by_crit = {v.criterion: v for v in verdicts}
                v = by_crit.get(crit)
                if v is None:
                    continue
                frac_a += _verdict_weight(v.winner, v.margin) * w
                total_w += w
            if total_w == 0.0:
                result.append(PairwiseVerdict(
                    criterion=crit, winner="tie", margin="slight",
                    rationale="", evidence_a="", evidence_b="",
                ))
                continue
            frac = frac_a / total_w
            if frac > 0.6:
                winner = "A"
            elif frac < 0.4:
                winner = "B"
            else:
                winner = "tie"
            margin = "decisive" if abs(frac - 0.5) > 0.3 else ("clear" if abs(frac - 0.5) > 0.15 else "slight")
            result.append(PairwiseVerdict(
                criterion=crit, winner=winner, margin=margin,
                rationale=f"Fractional win for A: {frac:.3f}",
                evidence_a="", evidence_b="",
            ))
        return tuple(result)
