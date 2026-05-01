import { useEffect, useState } from "react";

import { api } from "../api";
import type {
	BenchmarkRunItem,
	EvaluationRunItem,
	RunListItem,
} from "../types";

type Props = {
	scenarioId: string;
	onViewRun: (evalId: string, benchId: string) => void;
};

export function RunsMasterDetail({ scenarioId, onViewRun }: Props) {
	const [runs, setRuns] = useState<BenchmarkRunItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [evals, setEvals] = useState<EvaluationRunItem[] | null>(null);
	const [loadingEvals, setLoadingEvals] = useState(false);

	// Load scenario to get revision IDs, then load matching benchmark runs
	useEffect(() => {
		setLoading(true);
		setError(null);
		api
			.getScenario(scenarioId)
			.then((scenario) => {
				const revIds = scenario.revisions.map((r) => r.id);
				return api
					.listRuns({ page_size: 200 })
					.then((res) => ({ res, revIds }));
			})
			.then(({ res, revIds }) => {
				const benchmarks: BenchmarkRunItem[] = res.items
					.filter((r: RunListItem) =>
						r.kind === "benchmark" && revIds.length > 0
							? revIds.includes(r.scenario_revision_id)
							: false,
					)
					.map(
						(r: RunListItem) =>
							({
								id: r.id,
								scenario_revision_id: r.scenario_revision_id,
								verified_setup_run_id: r.verified_setup_run_id || "",
								status: r.status,
								subject_count: r.subject_count || 0,
								started_at: r.started_at,
								finished_at: r.finished_at,
								created_at: r.created_at,
								metadata_json: null,
							}) as BenchmarkRunItem,
					);
				setRuns(benchmarks);
			})
			.catch((e) => setError(e.message || "Failed to load runs"))
			.finally(() => setLoading(false));
	}, [scenarioId]);

	// Auto-select the first run
	useEffect(() => {
		if (!selectedId && runs.length > 0) {
			setSelectedId(runs[0].id);
		}
	}, [runs, selectedId]);

	// Load evaluations when a run is selected
	useEffect(() => {
		if (!selectedId) {
			setEvals(null);
			return;
		}
		setLoadingEvals(true);
		api
			.listBenchmarkEvaluations(selectedId)
			.then(setEvals)
			.catch(() => setEvals([]))
			.finally(() => setLoadingEvals(false));
	}, [selectedId]);

	const selectedRun = runs.find((r) => r.id === selectedId);

	return (
		<div className="runs-master-detail">
			<aside className="runs-list">
				<div className="runs-list__header">
					<h3>Runs</h3>
				</div>
				{error ? <div className="error-banner">{error}</div> : null}
				{loading && (
					<p
						style={{
							color: "var(--muted)",
							fontSize: 12,
							padding: "0.5rem 0.875rem",
						}}
					>
						Loading runs...
					</p>
				)}
				<ul>
					{runs.map((r) => (
						<li
							key={r.id}
							className={selectedId === r.id ? "active" : ""}
							onClick={() => setSelectedId(r.id)}
						>
							<span className={`status-dot status-${r.status}`} />
							<span className="runs-list__time">
								{new Date(r.created_at).toLocaleString()}
							</span>
							<span className="runs-list__status">{r.status}</span>
						</li>
					))}
					{!loading && runs.length === 0 && (
						<li className="empty-state-item">No benchmark runs yet</li>
					)}
				</ul>
			</aside>
			<main className="run-detail-pane">
				{!selectedId && (
					<div
						className="empty-state"
						style={{ minHeight: "auto", height: "100%" }}
					>
						Select a run to see evaluations
					</div>
				)}
				{selectedId && selectedRun && (
					<div>
						<div className="run-detail-pane__header">
							<span className="eyebrow">Benchmark Run</span>
							<span
								style={{
									fontFamily: "var(--mono)",
									fontSize: 11,
									color: "var(--text)",
								}}
							>
								{selectedRun.id.slice(0, 8)}
							</span>
							<span
								className={`status-badge status-${selectedRun.status}`}
								style={{
									color:
										selectedRun.status === "completed"
											? "var(--green)"
											: selectedRun.status === "failed"
												? "var(--danger)"
												: "var(--accent)",
								}}
							>
								{selectedRun.status}
							</span>
						</div>
						{loadingEvals && (
							<p
								style={{
									color: "var(--muted)",
									fontSize: 12,
									padding: "0 1rem",
								}}
							>
								Loading evaluations...
							</p>
						)}
						{!loadingEvals && evals && evals.length === 0 && (
							<p
								style={{
									color: "var(--muted)",
									fontSize: 12,
									padding: "0 1rem",
								}}
							>
								No evaluations yet.
							</p>
						)}
						{!loadingEvals && evals && evals.length > 0 && (
							<ul className="eval-list">
								{evals.map((ev) => (
									<li key={ev.id} className="eval-list__item">
										<div className="eval-list__info">
											<span className={`status-dot status-${ev.status}`} />
											<span className="eval-list__id">{ev.id.slice(0, 8)}</span>
											<span className="eval-list__status">
												{ev.status}
												{ev.repair_success !== null && (
													<span className="eval-list__repair">
														{" "}
														&middot; repair: {ev.repair_success ? "yes" : "no"}
													</span>
												)}
											</span>
										</div>
										<button
											className="ghost-button compact"
											style={{ fontSize: 11, padding: "3px 8px" }}
											onClick={() => onViewRun(ev.id, selectedRun.id)}
										>
											View transcript
										</button>
									</li>
								))}
							</ul>
						)}
					</div>
				)}
			</main>
		</div>
	);
}
