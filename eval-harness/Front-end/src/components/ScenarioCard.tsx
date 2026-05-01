import type { ScenarioListItem } from "../types";

type Props = {
	scenario: ScenarioListItem;
	onSelect: (id: string) => void;
};

export function ScenarioCard({ scenario, onSelect }: Props) {
	const lastRun = scenario.last_run_at
		? new Date(scenario.last_run_at).toLocaleString()
		: "never";
	const runCount = scenario.run_count ?? scenario.benchmark_run_count;
	return (
		<button
			type="button"
			className="scenario-card"
			onClick={() => onSelect(scenario.id)}
		>
			<div className="scenario-card__header">
				<span className="scenario-card__title">{scenario.title}</span>
				<span className={`status-badge status-${scenario.lifecycle_status}`}>
					{scenario.lifecycle_status}
				</span>
			</div>
			<div className="scenario-card__meta">
				<span>{runCount} runs</span>
				<span>last: {lastRun}</span>
			</div>
			{scenario.tags?.length ? (
				<div className="scenario-card__tags">
					{scenario.tags.slice(0, 3).map((t) => (
						<span key={t} className="tag-chip">
							{t}
						</span>
					))}
				</div>
			) : null}
		</button>
	);
}
