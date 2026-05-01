import { useEffect, useState } from "react";
import { api } from "../api";
import type { ScenarioListItem } from "../types";
import ScenarioDetail from "./ScenarioDetail";

export default function Scenarios() {
	const [scenarios, setScenarios] = useState<ScenarioListItem[]>([]);
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		api
			.listScenarios()
			.then(setScenarios)
			.catch((e) =>
				setError(e instanceof Error ? e.message : "Failed to load scenarios"),
			)
			.finally(() => setLoading(false));
	}, []);

	const statusDot = (status: string) => {
		if (status === "verified") return "dot-green";
		if (status === "failed") return "dot-red";
		return "dot-muted";
	};

	return (
		<div className="scenarios-layout">
			<aside className="scenarios-sidebar">
				<div className="scenarios-sidebar-header">
					<h3 className="eyebrow">Scenarios</h3>
					<span className="mono muted" style={{ fontSize: 11 }}>
						{scenarios.length}
					</span>
				</div>
				{loading && <p className="lede">Loading...</p>}
				{error && <div className="error-banner">{error}</div>}
				<div className="scenarios-list">
					{scenarios.map((s) => (
						<button
							key={s.id}
							className={`scenario-item ${selectedId === s.id ? "active" : ""}`}
							onClick={() => setSelectedId(s.id)}
						>
							<div className="scenario-item-main">
								<span
									className={`status-dot-sm ${statusDot(s.verification_status)}`}
								/>
								<div className="scenario-item-text">
									<strong>{s.title}</strong>
									<small className="mono">{s.scenario_name}</small>
								</div>
							</div>
							<div className="scenario-item-meta">
								<span className="mono" style={{ fontSize: 10 }}>
									{s.benchmark_run_count} runs
								</span>
							</div>
						</button>
					))}
					{!loading && scenarios.length === 0 && (
						<p className="lede" style={{ padding: "0 11px" }}>
							No scenarios yet. Create one from the Generate / Edit tab.
						</p>
					)}
				</div>
			</aside>
			<main className="scenarios-main">
				{selectedId ? (
					<ScenarioDetail scenarioId={selectedId} />
				) : (
					<div className="empty-state">
						<p className="lede">Select a scenario to view details</p>
					</div>
				)}
			</main>
		</div>
	);
}
