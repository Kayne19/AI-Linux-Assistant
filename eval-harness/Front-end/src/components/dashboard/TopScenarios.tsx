import { useScenarios } from "../../hooks/useScenarios";

export function TopScenarios({ onSelect }: { onSelect: (id: string) => void }) {
	const { scenarios } = useScenarios();
	const top = [...(scenarios ?? [])]
		.sort((a, b) => (b.benchmark_run_count ?? 0) - (a.benchmark_run_count ?? 0))
		.slice(0, 5);

	return (
		<div className="widget">
			<div className="widget__title">Top scenarios</div>
			{top.length === 0 && (
				<div
					className="empty-state"
					style={{ padding: "0.5rem", minHeight: 60 }}
				>
					<p className="lede" style={{ margin: 0 }}>
						No scenarios yet
					</p>
				</div>
			)}
			<ol className="ranked-list">
				{top.map((s) => (
					<li key={s.id}>
						<button
							type="button"
							className="link"
							onClick={() => onSelect(s.id)}
						>
							{s.title}
						</button>
						<span className="muted">{s.benchmark_run_count ?? 0} runs</span>
					</li>
				))}
			</ol>
		</div>
	);
}
